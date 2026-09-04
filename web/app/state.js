/* The mimiwatch screen — shared state and small helpers. Every other file uses
 * what is here -- which is why it is read first.
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope -- module
 * syntax is avoided for the same reason as in overlay.js, which is shared with
 * the extension. Everything they call on each other is a function call at run
 * time, so the file order only has to keep main.js last. */

/* mimiwatch player logic.
 *
 * The recorded-video flow needs no clock negotiation: every cue carries a
 * media-relative timestamp from transcription, and the YouTube iframe API
 * reports the same clock through getCurrentTime(). Lining them up is a
 * lookup, not an estimate -- the offset slider exists only for live streams
 * and for a viewer who wants to nudge it by taste.
 */
const $ = (id) => document.getElementById(id);

const state = {
  doc: null, cues: [], idx: -1, player: null, ready: false,
  mode: "both", offset: 0, showPrev: true, follow: true, panelHidden: false,
  libraryHidden: false, scriptOnly: false, capture: null, scriptWin: null,
  // What happens when a line in the script is clicked. The default is what it
  // has always been -- "seek there". It is set on every open -- left behind, the
  // next visitor comes to read, misclicks, and gets the editor.
  scriptMode: "read",
  // The cue numbers picked in translation mode. A Set -- the order is held by
  // the script, so all that is needed here is whether a line was picked.
  picked: new Set(), pickAnchor: null,
  genres: [],
  // The player height just before entering fullscreen. The baseline for the
  // subtitle size scale.
  fsBaseHeight: 0, cuePx: 0,
  cuePos: null,        // { x, y } as ratios. restore() fills it -- see DEFAULT_CUE_POS
  backend: "local-gemma", asr: "tcpp-best", refine: true,
  backends: [], asrBackends: [], liveProfiles: [], jobId: null,
  models: null,        // the answer from /api/models. Model and tool list and state (engines.js)
  live: null,          // { id, es, store } while a broadcast is running (store: MimiCues)
  // Multiview (tiles.js). tiles are the panes on screen, focus is the pane the
  // audio, subtitles and subtitle log follow, mv is the server's group
  // { id, focus, members }, mvLayout is the chosen layout name.
  tiles: [], focus: null, mv: null, mvLayout: "",
};

/* A cue can hold one translation per backend, so switching backends is a
 * lookup when the work has already been done and a job when it has not. */
const trOf = (c) => c && c.translations ? c.translations[state.backend] : null;

/* Two different questions get asked about live, and conflating them is what
 * made "Stop" leave the screen inconsistent with itself:
 *   state.live  -- a transcription session is running right now
 *   isLiveDoc() -- what the player is showing is a broadcast
 * Stopping subtitles answers only the first. The broadcast keeps playing, so
 * everything that reads "this is not a recording" -- cue lookup, script rows
 * keyed by id, the picker entry -- has to keep saying yes afterwards. */
const isLiveDoc = () => !!(state.doc && state.doc.live);

/* Is this a live stream that is **still being received**? A finished broadcast
 * has to be handled differently.
 *
 * YouTube leaves a broadcast behind as a recording once it ends. From that
 * moment on this session's subtitles are not live subtitles but the subtitles
 * of that recording -- they have to be found by time, and the times are right
 * (they were written down media-relative).
 *
 * If the state has not arrived yet, treat it as still receiving. That is true
 * for an instant right after the start, and looking up by time then means
 * looking for subtitles that have not arrived. */
const isLiveReceiving = () => !!state.live
  && (!state.live.state || LIVE_RUNNING.includes(state.live.state));

/* A speaker chip is only information when it distinguishes someone. CAM++
 * gives one embedding per segment, so a four-way collab mixed into a single
 * stream comes back as S1 for every line -- a label that decorates without
 * telling the reader anything. Hold the chips until a second speaker
 * actually appears, and show them from then on. */
function showSpeakers() {
  const set = state.live ? state.live.speakers
            : new Set(state.cues.map(c => c.speaker).filter(Boolean));
  return !!set && set.size >= 2;
}

const chipFor = (c) => (showSpeakers() && c && c.speaker) ? c.speaker : "";

const PREFS = "mimiwatch.prefs";

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS)) || {}; }
  catch { return {}; }        // private windows and blocked storage both land here
}

function savePrefs(p) {
  try { localStorage.setItem(PREFS, JSON.stringify(p)); } catch { /* non-fatal */ }
}

/* ---------- subtitles on screen ----------
 *
 * The drawing is done by web/overlay.js. It is the same one set as the
 * subtitles the extension lays over the YouTube page -- it was pulled out so
 * that look, position and dragging are not fixed in two places. Here we only
 * feed that module its values and hand it a clock. */
/* The focused tile's overlay (tiles.js's initTiles/setFocus puts it here).
 * Every tile has an overlay of its own, but only the focused tile's is drawn. */
let overlay = null;

/* Tells the module what should be drawn right now. Called whenever the cue
 * list, the backend, or whether this is live changes. */
function syncOverlayData() {
  if (!overlay) return;
  overlay.setData({
    cues: state.cues, backend: state.backend,
    live: isLiveDoc(), receiving: isLiveReceiving(),
    speakers: showSpeakers(),
  });
}

function cueAt(t) {
  syncOverlayData();
  return overlay ? overlay.cueAt(t) : -1;
}

function renderCue() {
  if (!overlay || !state.player || !state.player.ready) return;
  syncOverlayData();
  overlay.setView({ mode: state.mode, showPrev: state.showPrev });
  const i = overlay.render(state.player.getCurrentTime() + state.offset);
  if (i !== state.idx) {
    state.idx = i;
    markScript(i);
  }
}

const cueStart = (c) => (c.start != null ? c.start : c.t) || 0;

/* Timestamps in the subtitle log. The same shape as the subtitle log the
 * extension raises on the YouTube page (ext/panel.js) -- `1:02:03` past an
 * hour, `02:03` otherwise. This used to write only minutes:seconds, so a
 * two-hour broadcast's `95:12` could not be lined up against the YouTube
 * progress bar's `1:35:12`. Both screens must point at the same utterance with
 * the same characters. */
const fmt = (s) => {
  const t = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), x = t % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(x)}` : `${two(m)}:${two(x)}`;
};

/* Nothing is saved before the settings have been fetched from the server and
 * lined up. The boot order is restore() → loadBackends(), so a single save in
 * between lets the initial values hard-coded in state overwrite what the user
 * chose. loadBackends then reads what was just overwritten, so the server's
 * defaults never win either. */
let booted = false;

function persist() {
  if (!booted) return;
  // This has to be written against the same test applyModeForDoc() reads. It
  // used to look at `doc.translated` here, but for live that value is false
  // until the first translation arrives, so a subtitle mode chosen during a
  // live stream was saved in the `modeSame` slot and read back from the
  // `modeTranslated` slot next time, which is to say not remembered.
  const translated = docHasTranslation();
  const prev = loadPrefs();
  savePrefs({
    ...prev,
    [translated ? "modeTranslated" : "modeSame"]: state.mode,
    size: +$("size").value, dim: +$("dim").value,
    cuePos: state.cuePos, showPrev: $("show-prev").checked,
    offset: +$("offset").value, viewerLang: $("viewer-lang").value,
    panelHidden: state.panelHidden, libraryHidden: state.libraryHidden,
    backend: state.backend,
    profile: (document.querySelector('#add-form select[name="profile"]') || {}).value,
    genre: (document.querySelector('#add-form select[name="genre"]') || {}).value,
    asr: state.asr, refine: state.refine,
    mvLayout: state.mvLayout,
  });
}

function restore() {
  const p = loadPrefs();
  if (p.viewerLang) $("viewer-lang").value = p.viewerLang;
  if (p.size) $("size").value = p.size;
  if (p.dim != null) $("dim").value = p.dim;
  // This used to write only the vertical position, as the "Position" slider's
  // percentage (p.pos). That value is carried over to centre-at-that-height,
  // so a position settled on back in the slider days does not jump to
  // bottom-centre on one update.
  const D = MimiOverlay.DEFAULT_POS;
  state.cuePos = p.cuePos ? { ...D, ...p.cuePos }
               : p.pos != null ? { x: D.x, y: p.pos / 100 }
               : { ...D };
  overlay.setPos(state.cuePos);
  state.mvLayout = p.mvLayout || "";
  if (p.showPrev != null) $("show-prev").checked = p.showPrev;
  if (p.offset != null) { $("offset").value = p.offset; state.offset = p.offset; }
  state.showPrev = $("show-prev").checked;
  applySize(+$("size").value);
  document.documentElement.style.setProperty("--cue-bg", (+$("dim").value / 100).toFixed(2));
  applyCuePos();
  $("offset-val").textContent = state.offset.toFixed(1) + "s";
  syncControlInputs();
  setScriptMode("read");     // Always start in read mode. Never saved.
  setScriptView(p.scriptView || "both");
  if (p.scriptSize) {
    $("script-size").value = p.scriptSize;
    $("script").style.setProperty("--script-size", p.scriptSize + "px");
  }
  setPanel(!!p.panelHidden);
  setLibrary(!!p.libraryHidden);
}

const esc = (t) => String(t).replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- live ---------- */
/* The server keeps live sessions and their subtitles in SQLite, so a session
 * outlives the tab. Where the state name used to reach the screen verbatim, put
 * words a person can read. */
const LIVE_RUNNING = ["starting", "loading", "running"];

// The state words the screen shows. The keys are protocol -- the server sends
// exactly these -- and only the values are language, so the map answers with
// whatever the string table says at the moment it is read. A plain object of
// translated strings would freeze at whatever language was current when this
// file loaded, and the callers read it minutes later, after a language switch.
const LIVE_STATE = {};
["starting", "loading", "running", "stopped", "interrupted", "error"].forEach(function (s) {
  Object.defineProperty(LIVE_STATE, s, {
    get: function () { return MW_I18N.t("live.state." + s); }, enumerable: true,
  });
});
