/* The mimiwatch screen — the YouTube player, the mode, size and position of the
 * subtitles on screen, and fullscreen.
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope -- module
 * syntax is avoided for the same reason as in overlay.js, which is shared with
 * the extension. Everything they call on each other is a function call at run
 * time, so the file order only has to keep main.js last. */

/* ---------- language status (R3.8/R3.9) ---------- */
function updateLangStatus() {
  const el = $("lang-status");
  if (!state.doc) { el.textContent = ""; return; }
  const src = state.doc.source_lang, viewer = $("viewer-lang").value;
  if (src === viewer) {
    el.className = "status";
    el.innerHTML = t("player.lang.same", { src });
  } else if (state.doc.translated && state.doc.viewer_lang === viewer) {
    el.className = "status";
    el.innerHTML = t("player.lang.translated", { src, viewer });
  } else {
    el.className = "status warn";
    el.innerHTML = t("player.lang.missing", { src, viewer });
  }
}

/* ---------- loading ---------- */
async function loadVideo(id) {
  const doc = await (await fetch(`/api/video/${id}`)).json();
  // A recording is watched in a single tile. Broadcasts the other tiles were watching are only detached from the screen.
  const t = soloTile();
  t.doc = doc; t.live = null; t.title = doc.title || "";
  state.doc = doc;
  state.cues = doc.cues || [];
  state.idx = -1;
  buildScript();
  updateLangStatus();
  renderBackendPicker();
  syncGenreToDoc();
  setNowTitle(doc.title);
  applyModeForDoc();
  clearPlayerError(t);
  // A local-file transcription is a video YouTube does not have. Open the endpoint where the server serves the original.
  const src = doc.source === "file"
    ? { site: "media", url: `/api/media/${encodeURIComponent(id)}` }
    : { site: "youtube", video_id: id };
  const a = t.adapter;
  if (a && a.kind === (src.site === "media" ? "media" : "youtube") && a.ready && a.load) a.load(src);
  else await mountTile(t, src);
  updateTileBar(t);
  syncMvControls();
}

/* Someone watching a talk in their own language does not turn subtitles on;
 * the script panel already carries the text. So the default follows the
 * language match, and each of the two cases remembers its own last choice. */
function docHasTranslation() {
  if (!state.doc) return false;
  // Same language means no translation will ever come, so subtitles start off.
  if (state.doc.source_lang && state.doc.source_lang === state.doc.viewer_lang) {
    return false;
  }
  // A live session has no cues yet at the moment this first runs; judging it
  // by what has arrived would leave subtitles off for a broadcast that is
  // about to be translated.
  if (state.live) return true;
  return state.cues.some(c => trOf(c));
}

function applyModeForDoc() {
  const translated = docHasTranslation();
  document.querySelectorAll("[data-needs-translation]").forEach(b => {
    b.hidden = !translated;
  });
  const p = loadPrefs();
  state.mode = translated ? (p.modeTranslated || "both") : (p.modeSame || "off");
  if (!translated && (state.mode === "both" || state.mode === "translation")) {
    state.mode = "off";
  }
  syncModeButtons();
  state.idx = -1;
  renderCue();
}

/* Waits for the IFrame API to finish loading. The player itself is made per
 * tile by the adapter (ytAdapter in adapters.js). */
let apiReady = null;

function whenApiReady() {
  if (apiReady) return apiReady;
  apiReady = new Promise((resolve) => {
    if (window.YT && window.YT.Player) return resolve();
    // The API calls this global when it finishes loading; the poll is the
    // safety net for the case where it fired before this script ran.
    window.onYouTubeIframeAPIReady = resolve;
    const timer = setInterval(() => {
      if (window.YT && window.YT.Player) { clearInterval(timer); resolve(); }
    }, 100);
    setTimeout(() => { clearInterval(timer); resolve(); }, 10000);
  });
  return apiReady;
}

/* Turns the codes YouTube hands out into words a person can read.
 *
 * Lumping them all into one "the video may have embedding blocked" leaves no
 * clue what to do about it. Members-only broadcasts in particular usually have
 * embedding blocked and come back as 101/150, and that is not a problem we can
 * fix but a case for watching it on YouTube. The server writes the subtitles
 * down separately, so the script on the right still reads fine then. */
function embedErrorText(code) {
  if (code === 101 || code === 150) return t("player.embed.blocked");
  if (code === 100) return t("player.embed.notFound");
  if (code === 5) return t("player.embed.playbackFailed");
  if (code === 2) return t("player.embed.badId");
  return t("player.embed.unknown", { code });
}

/* The notice box covers the whole tile. Left in place, the next video chosen
 * plays behind it, leaving a state where the sound is there but the screen is a
 * notice. With no tile given, it is the focused tile. */
function clearPlayerError(tile = focusedTile()) {
  if (!tile) return;
  const box = tile.el.querySelector(":scope > .player-error");
  if (box) box.remove();
}

function playerError(msg, videoId, tile = focusedTile()) {
  if (!tile) return;
  let box = tile.el.querySelector(":scope > .player-error");
  if (!box) {
    box = document.createElement("div");
    box.className = "player-error";
    tile.el.appendChild(box);
  }
  box.textContent = msg;
  if (videoId) {
    const a = document.createElement("a");
    a.href = `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = t("player.error.openOnYouTube");
    a.className = "seg";
    box.append(document.createElement("br"), a);
  }
}

/* ---------- controls ---------- */
/* There are two sets of subtitle mode buttons -- the ones under the player and
 * the ones inside the fullscreen box. Whichever is pressed goes through the
 * same place here, and both are kept showing the same thing. */
function setMode(mode) {
  state.mode = mode;
  syncModeButtons();
  persist();
  state.idx = -1;
  renderCue();
}

function syncModeButtons() {
  document.querySelectorAll("[data-mode]").forEach(b =>
    b.classList.toggle("on", b.dataset.mode === state.mode));
}

const CONTROL_EFFECT = {
  size: v => applySize(+v),
  dim:  v => document.documentElement.style.setProperty("--cue-bg", (+v / 100).toFixed(2)),
};

function setControl(name, value) {
  $(name).value = value;
  CONTROL_EFFECT[name](value);
  syncControlInputs();
  persist();
}

/* Mirrors the windowed values into the fullscreen inputs. Called once on
 * entering fullscreen too -- untouched until then, those inputs sit empty. */
function syncControlInputs() {
  for (const name of ["size", "dim"]) {
    document.querySelectorAll(`[data-ctl="${name}"]`).forEach(el => {
      if (el.value !== $(name).value) el.value = $(name).value;
    });
  }
}

function applySize(px) {
  state.cuePx = px;
  applyCueSize();
}

/* The subtitle size grows **by however much the player grew**, not by the
 * slider value alone.
 *
 * Use the 30px chosen in the window as it is in fullscreen and the subtitles
 * look smaller by exactly the three or four times the screen grew. The box
 * height at the moment fullscreen is entered is taken as the baseline and the
 * size is multiplied by that ratio. In windowed mode there is no baseline, so
 * the scale is 1 and it behaves exactly as it always has. */
function applyCueSize() {
  const t = focusedTile();
  if (!overlay || !t) return;
  const px = state.cuePx || +$("size").value;
  // The baseline for the scale is the **focused tile's** height just before
  // entering fullscreen. What to take as the baseline is the caller's decision
  // -- the extension uses YouTube's own fullscreen, so its baseline is a
  // different one. In windowed mode the whole box is the baseline, so one tile
  // gives a scale of 1 (as it always was) and split four ways it shrinks to fit
  // that cell.
  const base = state.fsBaseHeight || $("player-wrap").clientHeight;
  const h = t.el.clientHeight;
  overlay.setSize(px, base && h ? h / base : 1);
}

/* ---------- subtitle position ----------
 *
 * Why it is written down as ratios, the clamping rules, and why dragging needs
 * pointer capture are all in web/overlay.js. All that happens here is feeding
 * the saved value into that module and wiring up the reset button. */
function applyCuePos() {
  if (overlay) overlay.reflow();
}

function resetCuePos() {
  if (overlay) overlay.resetPos();   // onPos takes care of state.cuePos and of saving
}

const fsElement = () => document.fullscreenElement || document.webkitFullscreenElement;

function toggleFullscreen() {
  const wrap = $("player-wrap");
  if (fsElement()) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  } else {
    // Remembers the focused tile's height just before entering fullscreen.
    // Measured after entering, the value has already grown and the scale comes
    // out as 1.
    const t = focusedTile();
    state.fsBaseHeight = (t ? t.el : wrap).clientHeight;
    const req = wrap.requestFullscreen || wrap.webkitRequestFullscreen;
    // MW_I18N.t, not the short `t` -- in this scope `t` is the focused tile.
    if (!req) { fsFailed(MW_I18N.t("player.fullscreen.unsupported")); return; }
    Promise.resolve(req.call(wrap)).catch(err => fsFailed(err.message));
  }
}

/* Reports the failure at the button itself. jobError writes "re-translation
 * failed" and is for translation jobs, so it does not fit here. */
function fsFailed(msg) {
  state.fsBaseHeight = 0;
  const b = $("fullscreen");
  b.textContent = t("player.fullscreen.unavailable");
  b.title = msg;
  console.error("[fullscreen]", msg);
  setTimeout(() => { b.textContent = t("player.fullscreen.enter"); }, 4000);
}

let fsIdleTimer = null;

function showFsControls() {
  if (!fsElement()) return;
  const wrap = $("player-wrap");
  wrap.classList.add("fs-active");
  // On the first opening after entering fullscreen, take up whatever was changed in the window meanwhile.
  syncControlInputs();
  clearTimeout(fsIdleTimer);
  fsIdleTimer = setTimeout(hideFsControls, 2500);
}

function hideFsControls() {
  $("player-wrap").classList.remove("fs-active");
}

/* The cursor is not hidden.
 *
 * While the cursor is over the iframe its shape is decided by the YouTube
 * document, so there is no way to set cursor from outside. Laying a transparent
 * film over it to take the cursor was tried and did not work -- the cursor
 * shape is re-evaluated **when the pointer moves**, and a film laid down after
 * it stopped has no effect until the next movement, which is the very movement
 * that takes the film away again. On top of that, clicks while the film is
 * there never reach YouTube, so it was paying the price without the effect. */
function onFullscreenChange() {
  const el = fsElement();
  const on = !!el;
  // If the iframe goes fullscreen the subtitles are outside its subtree and
  // disappear. Getting this far means the defences above were breached, so this
  // is undone rather than passed over quietly.
  if (el && (el.tagName === "IFRAME" || el.tagName === "VIDEO")) {
    console.warn("[fullscreen] the player element went fullscreen. Subtitles are not visible.");
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    fsFailed(t("player.fullscreen.playerTookOver"));
    return;
  }
  if (!on) state.fsBaseHeight = 0;
  $("fullscreen").classList.toggle("on", on);
  $("fullscreen").textContent = on ? t("player.fullscreen.exit") : t("player.fullscreen.enter");
  if (on) {
    showFsControls();
  } else {
    clearTimeout(fsIdleTimer);
    $("player-wrap").classList.remove("fs-active");
  }
  // It has to be measured after the box has resized. Right after the switch it is still the old size.
  requestAnimationFrame(applyCueSize);
}

/* The language band and the fullscreen button label are written once -- when a
 * video is opened, and when fullscreen is entered or left. Switching the
 * language would leave both sitting in the old one. The band belongs to
 * renderLiveStatus while a live session is attached, so it is left alone then. */
MW_I18N.onChange(() => {
  if (!state.live) updateLangStatus();
  const b = $("fullscreen");
  if (b) b.textContent = fsElement() ? t("player.fullscreen.exit") : t("player.fullscreen.enter");
});
