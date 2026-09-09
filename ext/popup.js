/* The extension popup. It answers what to do about this tab.
 *
 *   - which of the subtitles already received to lay on
 *   - transcribe anew on this tab (from the URL, or from this tab's sound)
 *   - subtitle look
 *
 * Managing the library, engine settings and script editing are not here. The
 * mimiwatch page takes those as they are -- building them into the popup as
 * well would make two copies.
 *
 * Starting anew belongs here. The extension already knows which tab it is, so
 * there is no reason to paste a URL in again, and a members-only stream can
 * only be received from this tab's sound.
 */
const $ = (id) => document.getElementById(id);

const send = (msg) => new Promise((r) => chrome.runtime.sendMessage(msg, r));
const toTab = (tabId, msg) =>
  new Promise((r) => chrome.tabs.sendMessage(tabId, msg, () => r(chrome.runtime.lastError ? null : true)));

let tabId = null;
/* Is this tab YouTube. It sits outside init so that when the language changes,
 * "Open this on a YouTube tab" can be rewritten in that language -- that string
 * is drawn once the moment the popup opens and then stays. */
let onYouTube = false;
let prefs = { mode: "both", showPrev: true, size: 30, dim: 0.55, offset: 0,
              panel: false };
/* The values used when starting a new session. Kept apart from the subtitle
 * look (prefs) -- that side changes what is showing now, this side settles what
 * starts next. */
/* Refinement **starts switched off.** That differs from the mimiwatch page's
 * default. That side handles VODs too, while the extension only starts live, and
 * on live refinement is usually a loss -- it waits 2 seconds for one utterance
 * group to end, joins it and transcribes it again, so when the talk goes quickly
 * back and forth the subtitle settles late and a line already read changes
 * entirely. */
let start = { lang: "", genre: "general", refine: false, profile: "broadcast",
              // The language to translate into. It used to be nailed to "ko",
              // so only Korean speakers could use the extension. The same value
              // as "My language" on the mimiwatch page.
              viewerLang: "ko" };
const SKEY = "startPrefs";
/* The translation target remembered on the server (backends.json). The popup
 * reads it in fillChoices so the last choice made on either surface -- the web
 * page or the extension -- is the default here too. The local start.viewerLang
 * is the fallback for when the server was not reached. */
let serverViewer = "";

/* The language for the screen is held by the server (`ui_lang` in the config).
 * The popup does not know it the moment it comes up, so the table starts in
 * English and settles once the first query comes back -- which means the strings
 * we write have to be drawn after the language is settled, and redrawn if it
 * changes later (moving to a different server, and the like).
 *
 * What it works out is stored. The service worker and the content script have
 * nowhere to ask the server for a language (one has no screen, the other does
 * not reach the server directly) and their strings have to be in the same
 * language. */
const LANG_KEY = "uiLang";

async function useLang(code) {
  // An unknown code (the empty string included) has i18n go with the browser's
  // guess. Changing the table is this first line alone, so the caller has
  // nothing to wait for.
  const lang = MW_I18N.setLang(code || "");
  // The same value is not written again. storage raises a change notification
  // even for an unchanged value, and that notification wakes every attached
  // YouTube tab and the sleeping worker.
  const got = await chrome.storage.local.get(LANG_KEY);
  if (got[LANG_KEY] !== lang) chrome.storage.local.set({ [LANG_KEY]: lang });
}

/* Is it loading. While it is, a language change does not redraw. */
let loading = false;

/* When the language changes, what we wrote is rewritten. Fixed strings are
 * i18n's to touch, but the picker entries, the status line and the button names
 * are built here. While loading it is skipped -- loadFromServer redraws
 * everything in a moment. */
MW_I18N.onChange(() => {
  if (loading) return;
  if (!onYouTube) fail(t("popup.errNotYouTube"));
  syncProfileHint();
  syncResumeButton();
  syncHideButton();
  refreshState();
  refillPicker();
});

function fail(text) {
  $("err").textContent = text;
  $("err").hidden = !text;
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabId = tab && tab.id;
  onYouTube = !!(tab && /^https:\/\/www\.youtube\.com\//.test(tab.url || ""));
  if (!onYouTube) fail(t("popup.errNotYouTube"));

  const b = await send({ type: "base" });
  $("base").value = (b && b.data) || "http://localhost:8900";

  const got = await chrome.storage.local.get("overlayPrefs");
  if (got.overlayPrefs) Object.assign(prefs, got.overlayPrefs);
  $("size").value = prefs.size;
  $("dim").value = Math.round(prefs.dim * 100);
  $("offset").value = prefs.offset;
  $("offset-val").textContent = (+prefs.offset).toFixed(1) + "s";
  $("show-prev").checked = !!prefs.showPrev;
  $("panel").checked = !!prefs.panel;
  syncModes();

  const sp = await chrome.storage.local.get(SKEY);
  if (sp[SKEY]) Object.assign(start, sp[SKEY]);

  await loadFromServer();
  // Nothing to start if it is not a YouTube tab.
  $("start-box").classList.toggle("busy", !onYouTube);
}

/* Everything read from the server. Called on first open and when the "Server"
 * address is changed.
 *
 * Changing the address used to refill the picker alone. If the server was not on
 * 8900 and the first read failed, the genre and content type were left empty and
 * the change notification was still listening to the old address, so fixing the
 * address still left the popup unusable. */
async function loadFromServer() {
  loading = true;
  $("pick").length = 1;
  // The genres and content types are read **first**. The UI language rides on
  // that answer, so anything whose strings we build ourselves, like the picker
  // entries, has to be drawn after it -- with the order the other way round the
  // list alone stayed in English for a moment every time the popup opened.
  await fillChoices();
  await fillPicker();
  // The values are seated **after** the pickers are filled. A value put into an
  // empty select is simply thrown away -- which is why it looked as though it
  // went back to the start every time.
  $("lang").value = start.lang || "";
  $("genre").value = start.genre || "general";
  if (!$("genre").value) $("genre").selectedIndex = 0;
  $("refine").checked = !!start.refine;
  $("profile").value = start.profile || "broadcast";
  if (!$("profile").value) $("profile").selectedIndex = 0;
  // The remembered target comes from the server, so the last choice made on
  // either surface is the default. The local copy is the fallback for when the
  // server was not reached.
  const viewerVal = serverViewer || start.viewerLang || "ko";
  $("viewer").value = viewerVal;
  if (!$("viewer").value) $("viewer").selectedIndex = 0;
  if ($("viewer").value) start.viewerLang = $("viewer").value;
  syncProfileHint();
  await refreshState();
  await syncHideButton();
  watchServer();
  loading = false;
}

/* While the popup is open it takes the server's changes (a new session, state,
 * video) and refills the picker. The popup reads the list afresh every time it
 * opens, so that is usually enough, but a stream started on the mimiwatch page
 * or another tab while it was left open did not show. An extension page attaches
 * to the server without CORS thanks to host_permissions. */
let serverEs = null;
let refillTimer = null;

function watchServer() {
  if (serverEs) { serverEs.close(); serverEs = null; }
  try {
    serverEs = new EventSource($("base").value.replace(/\/+$/, "") + "/api/events");
  } catch (_) {
    return;
  }
  serverEs.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch (_) { return; }
    if (m.type !== "session" && m.type !== "video") return;
    // One arrives per subtitle line, so they are gathered for a moment and
    // refilled in one go.
    clearTimeout(refillTimer);
    refillTimer = setTimeout(refillPicker, 500);
  };
}

async function refillPicker() {
  const was = $("pick").value;
  $("pick").length = 1;
  await fillPicker();                       // afresh, from what the server knows and this tab watches
  if (!$("pick").value && was && [...$("pick").options].some((o) => o.value === was)) {
    $("pick").value = was;                  // what was selected stays
  }
  await syncHideButton();
  refreshState();
}

/* The genres and content types are held by the server. Nailed into the popup
 * they would fall out of step every time the server adds one or touches a value
 * -- especially the content type, whose table holds how many seconds it actually
 * splits at. */
let profiles = [];
/* The state of the sessions put in the picker. Used to bring "Resume" alive for
 * a stopped stream only. */
let sessionInfo = {};
const RUNNING = ["starting", "loading", "running"];

async function fillChoices() {
  const r = await send({ type: "backends" });
  // The UI language rides on this answer too. If the server was not reached it
  // goes with the browser's guess -- the error string that shows then has to be
  // in a language a person reads as well.
  useLang(r && r.ok ? r.data.ui_lang : "");
  serverViewer = (r && r.ok && r.data.viewer_lang) ? r.data.viewer_lang : "";
  if (!r || !r.ok) return;
  const g = $("genre");
  g.length = 0;                             // so a re-read after an address change does not pile up
  for (const x of (r.data.genres || [])) {
    g.append(new Option(MW_I18N.pick(x, "label") || x.id, x.id));
  }
  if (!g.length) g.append(new Option(t("popup.genreGeneral"), "general"));

  profiles = r.data.live_profiles || [];
  const p = $("profile");
  p.length = 0;
  for (const x of profiles) {
    p.append(new Option(MW_I18N.pick(x, "label") || x.id, x.id));
  }
  if (!p.length) p.append(new Option(t("popup.profileBroadcast"), "broadcast"));
}

/* Writes down how many seconds the chosen type actually splits at. Not knowing
 * that "General stream" is 4 s leaves no way to tell why the subtitles come out
 * in such small pieces. */
function syncProfileHint() {
  const x = profiles.find((p) => p.id === $("profile").value);
  $("profile-hint").textContent = x
    ? t("popup.hintProfile", { max: x.max_speech, min: x.min_silence })
    : t("popup.hintProfileNone");
}

/* What the server holds, in one list. Live sessions on top, VODs below -- what
 * you want to lay on now is usually whatever was just transcribed. */
async function fillPicker() {
  const sel = $("pick");
  const [s, v] = await Promise.all([send({ type: "sessions" }), send({ type: "videos" })]);
  if (!s || !s.ok) {
    fail(t("popup.errNoServer",
           { error: (s && s.error) || t("popup.errNoResponse") }));
    return;
  }
  fail("");
  sessionInfo = {};
  for (const x of (s.data || []).filter((x) => x.cues || RUNNING.includes(x.state))) {
    sessionInfo[x.id] = x;
    const running = RUNNING.includes(x.state);
    // A stopped one says so, to make visible what "Resume" points at. The tail
    // is not joined on separately; the whole entry is one string -- word order
    // differs by language, so building it out of halves is right in one language
    // only.
    const key = running ? "popup.pickLines"
      : x.stopped_by === "ended" ? "popup.pickLinesEnded" : "popup.pickLinesStopped";
    sel.append(new Option(t(key, { title: x.title || x.id, n: x.cues || 0 }),
                          "live:" + x.id));
  }
  for (const x of (v && v.data) || []) {
    sel.append(new Option(t("popup.pickLines", { title: x.title || x.id, n: x.cues }),
                          x.id));
  }
  const now = await send({ type: "watching", tabId });
  if (now && now.data) sel.value = now.data;
  syncResumeButton();
}

/* When what is picked is a stopped stream, the start buttons turn into resume. A
 * finished stream has nothing to resume -- transcribing the whole video is done
 * on the mimiwatch page. */
function syncResumeButton() {
  const v = $("pick").value || "";
  const st = v.startsWith("live:") ? sessionInfo[v.slice(5)] : null;
  const can = !!st && !RUNNING.includes(st.state) && st.stopped_by !== "ended";
  // With a stopped stream picked, **both** "Transcribe anew on this tab" buttons become resume --
  // the source can be switched on resume (a stream received from a URL that turns members-only,
  // onto the tab's sound). Pressing "From this URL" in that state used to split the same stream
  // off into a new session.
  $("start-url").textContent = t(can ? "popup.resumeFromUrl" : "popup.fromUrl");
  $("start-tab").textContent = t(can ? "popup.resumeFromTab" : "popup.fromTab");
  $("start-url-cookies").textContent =
    t(can ? "popup.resumeWithCookies" : "popup.fromUrlWithCookies");
  // "it stopped because the stream cut out" is not tacked onto the end of the string; that case gets its own.
  $("start-hint").textContent = can
    ? t(st.stopped_by === "stream" ? "popup.hintResumePickStream" : "popup.hintResumePick")
    : st && st.stopped_by === "ended" ? t("popup.hintEnded")
    : t("popup.hintStart");
}

async function refreshState() {
  if (!tabId) return;
  chrome.tabs.sendMessage(tabId, { type: "state" }, (r) => {
    if (chrome.runtime.lastError || !r) {
      $("state").textContent = t("popup.stateNotAttached");
      return;
    }
    if (!r.mounted) { $("state").textContent = t("popup.statePickToOverlay"); return; }
    // "It does not show" means several things. Whether it is attached, whether
    // it has a size, whether there is a subtitle to draw -- they are reported
    // apart, because that is what tells you where to look.
    const bits = [t("popup.stateCues", { n: r.cues })];
    bits.push(t(r.live ? (r.receiving ? "popup.stateReceiving" : "popup.stateStreamEnded")
                       : "popup.stateVod"));
    if (!r.player) bits.push(t("popup.stateNoPlayer"));
    else if (!r.box || !r.box.w) bits.push(t("popup.stateNoRoom"));
    else if (!r.text) bits.push(t(r.mode === "off" ? "popup.stateOff" : "popup.stateNoCue"));
    if (r.stalled) bits.push(t("popup.stateStalled"));
    if (!r.ticking) bits.push(t("popup.stateNoClock"));
    if (r.panel) bits.push(t(r.panelUp ? "popup.statePanelUp" : "popup.statePanelNoRoom"));
    $("state").textContent = bits.join(" · ");
  });
}

function syncModes() {
  document.querySelectorAll("[data-mode]").forEach((b) =>
    b.classList.toggle("on", b.dataset.mode === prefs.mode));
}

async function pushPrefs() {
  await chrome.storage.local.set({ overlayPrefs: prefs });
  if (tabId) await toTab(tabId, { type: "prefs", prefs });
}

$("pick").addEventListener("change", async (e) => {
  syncResumeButton();
  const r = await send({ type: "watch", tabId, value: e.target.value });
  if (r && !r.ok) { fail(r.error || t("popup.errAttach")); return; }
  // The page was not holding ours yet, so it was reloaded. Doing that quietly
  // looks like the screen came back by itself, so it says so.
  if (r && r.reloaded) $("state").textContent = t("popup.stateReloaded");
  await syncHideButton();
  setTimeout(refreshState, r && r.reloaded ? 1800 : 600);
});

document.querySelectorAll("[data-mode]").forEach((b) =>
  b.addEventListener("click", () => { prefs.mode = b.dataset.mode; syncModes(); pushPrefs(); }));

$("size").addEventListener("input", (e) => { prefs.size = +e.target.value; pushPrefs(); });
$("dim").addEventListener("input", (e) => { prefs.dim = +e.target.value / 100; pushPrefs(); });
$("offset").addEventListener("input", (e) => {
  prefs.offset = +e.target.value;
  $("offset-val").textContent = prefs.offset.toFixed(1) + "s";
  pushPrefs();
});
$("show-prev").addEventListener("change", (e) => { prefs.showPrev = e.target.checked; pushPrefs(); });
$("panel").addEventListener("change", (e) => {
  prefs.panel = e.target.checked; pushPrefs(); setTimeout(refreshState, 500);
});
$("reset-pos").addEventListener("click", () => { prefs.pos = null; pushPrefs(); });
/* One button was doing two jobs. It said "Put away" and only took it off the
 * screen, and whoever pressed it thought transcription had ended. It was still
 * running on the server. They are split apart.
 *
 * The taking-down side is a **toggle**. Taking it down with no way back leaves
 * nothing but finding it in the list and picking it again, and once twenty-odd
 * sessions have piled up that is work. The picker answers "what", and this
 * button answers "is it on the screen now" -- so taking it down leaves the
 * picker as it was. */
async function syncHideButton() {
  const now = await send({ type: "watching", tabId });
  const on = !!(now && now.data);
  $("hide").textContent = t(on ? "popup.hide" : "popup.show");
  $("hide").title = t(on ? "popup.hideTitle" : "popup.showTitle");
  // Nothing to press if there is nothing to lay on.
  $("hide").disabled = !on && !$("pick").value;
  return on;
}

$("hide").addEventListener("click", async () => {
  const on = !!(await send({ type: "watching", tabId }))?.data;
  await send({ type: "watch", tabId, value: on ? "" : $("pick").value });
  await syncHideButton();
  setTimeout(refreshState, 400);
});

$("stop").addEventListener("click", async () => {
  const value = $("pick").value;
  const id = value.startsWith("live:") ? value.slice(5) : "";
  if (!id) { fail(t("popup.errNotSession")); return; }
  $("stop").disabled = true;
  const r = await send({ type: "stopSession", sessionId: id, tabId });
  $("stop").disabled = false;
  if (r && !r.ok) { fail(r.error || t("popup.errStop")); return; }
  fail("");
  // It comes down from the screen too. Subtitles still up when transcription
  // has ended look as though it is still running. What piled up stays on the
  // server and can be picked again.
  //
  // Here the picker is emptied as well. Unlike taking it down, that session is
  // no longer being received, so "Put back on page" has nothing to point at.
  await send({ type: "watch", tabId, value: "" });
  $("pick").value = "";
  await syncHideButton();
  $("start-hint").textContent = t("popup.hintStopped");
  $("pick").length = 1;
  await fillPicker();
  setTimeout(refreshState, 400);
});
/* Appends onto the same session that was stopped. On live the user often pauses
 * for a moment and comes back, and the server dies and cuts it off as well.
 * Starting a new session then splits the subtitles into two sets, so it has to
 * be possible to resume from the extension too. For a session received from the
 * tab's sound it captures this tab's sound again -- the server cannot rewind
 * that sound. */
/* An empty `source` resumes from the stored source; "tab"/"hls" switches to that source. */
async function resumePicked(source, tab) {
  const value = $("pick").value;
  const id = value.startsWith("live:") ? value.slice(5) : "";
  if (!id) return;
  $("start-box").classList.add("busy");
  $("start-hint").textContent = t("popup.hintResuming");
  fail("");
  const r = await send({ type: "resumeSession", sessionId: id, tabId, source: source || "",
                         url: tab ? tab.url : "" });
  $("start-box").classList.remove("busy");
  if (!r || !r.ok) {
    fail((r && r.error) || t("popup.errResume"));
    $("start-hint").textContent = "";
    syncResumeButton();
    return;
  }
  $("start-hint").textContent =
    t(r.source === "tab" ? "popup.hintResumedTab" : "popup.hintResumedUrl");
  $("pick").length = 1;
  await fillPicker();
  $("pick").value = "live:" + id;
  syncResumeButton();
  await syncHideButton();
  setTimeout(refreshState, 800);
}

/* ---------- starting anew on this tab ---------- */

/* If the session in the picker is a stopped one, it appends onto that session
 * instead of starting anew. How it is started is the source it resumes from --
 * which may differ from the stored source. */
function pickedResumable() {
  const v = $("pick").value || "";
  const st = v.startsWith("live:") ? sessionInfo[v.slice(5)] : null;
  if (!st || RUNNING.includes(st.state) || st.stopped_by === "ended") return null;
  return st;
}

async function startWith(type, opts = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  if (opts.cookies) {
    // A members-only stream: it hands this browser's login cookies to the server right now and
    // then goes from the URL. Not a switch left on, just this once -- the file left on the
    // server is erased on the mimiwatch page.
    $("start-hint").textContent = t("popup.hintPushingCookies");
    const c = await send({ type: "pushCookies" });
    if (!c || !c.ok) { fail((c && c.error) || t("popup.errCookies")); $("start-hint").textContent = ""; return; }
  }
  if (pickedResumable()) {
    await resumePicked(type === "startCapture" ? "tab" : "hls", tab);
    return;
  }
  $("start-box").classList.add("busy");
  fail("");
  $("start-hint").textContent = t("popup.hintStarting");
  const r = await send({
    type, tabId: tab.id, url: tab.url,
    // The tab title is the session name. The extension can simply read the
    // title Chrome hides -- on the page side the track label is an opaque
    // identifier, so it only ever stayed "Tab audio".
    title: (tab.title || "").replace(/\s+-\s+YouTube$/, ""),
    lang: start.lang || null, genre: start.genre || "general",
    refine: !!start.refine, profile: start.profile || "broadcast",
    viewerLang: start.viewerLang || "ko",
  });
  $("start-box").classList.remove("busy");
  if (!r || !r.ok) {
    fail((r && r.error) || t("popup.errStart"));
    $("start-hint").textContent = "";
    return;
  }
  $("start-hint").textContent = t(r.reloaded
    ? "popup.hintStartedReloaded"
    : r.skippedReload
      // The tab's sound is being captured, so it was not reloaded. The subtitle
      // will not attach to the screen then, so it says what to do about it.
      ? "popup.hintStartedNeedsReload"
      : "popup.hintStarted");
  $("pick").length = 1;
  await fillPicker();
  $("pick").value = "live:" + r.id;
  await syncHideButton();
  setTimeout(refreshState, 800);
}

function saveStart() { chrome.storage.local.set({ [SKEY]: start }); }
$("lang").addEventListener("change", (e) => { start.lang = e.target.value; saveStart(); });
$("genre").addEventListener("change", (e) => { start.genre = e.target.value; saveStart(); });
$("refine").addEventListener("change", (e) => { start.refine = e.target.checked; saveStart(); });
$("profile").addEventListener("change", (e) => {
  start.profile = e.target.value; saveStart(); syncProfileHint();
});
$("viewer").addEventListener("change", (e) => {
  start.viewerLang = e.target.value; saveStart();
  // Remember it on the server too, so the web page picks it up. A failed write
  // only means the next open asks again.
  send({ type: "setViewer", lang: e.target.value });
});

$("start-url").addEventListener("click", () => startWith("startUrl"));
$("start-url-cookies").addEventListener("click", () => startWith("startUrl", { cookies: true }));
$("start-tab").addEventListener("click", () => startWith("startCapture"));

/* Changing the server address re-reads everything. The service worker reads the
 * stored address on every request, and the attached YouTube tabs are made to
 * reattach on the new address by the background. */
$("base").addEventListener("change", async (e) => {
  const base = e.target.value.trim().replace(/\/+$/, "") || "http://localhost:8900";
  e.target.value = base;
  await send({ type: "setBase", base });
  fail("");
  await loadFromServer();
});

init();
