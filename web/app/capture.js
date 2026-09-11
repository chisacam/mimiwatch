/* The mimiwatch screen — capturing tab audio (getDisplayMedia → 16kHz PCM →
 * /api/ingest) and naming a tab session.
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope -- module
 * syntax is avoided for the same reason as in overlay.js, which is shared with
 * the extension. Everything they call on each other is a function call at run
 * time, so the file order only has to keep main.js last. */

/* ---------- capturing tab audio ----------
 *
 * A members-only stream is one the server cannot receive. Feeding cookies to
 * yt-dlp does not help either -- YouTube keeps swapping the open tab's cookies
 * out, and that road skirts the terms of service to begin with.
 *
 * Instead the sound the user is already listening to is captured. The user
 * picks the tab themselves in the sharing dialog, so it is not a way round
 * anything, and no cookies are needed. Chromium browsers only -- they are the
 * only ones that offer tab audio sharing.
 *
 * What the server receives is 16kHz mono int16 PCM. Open the AudioContext at
 * 16000 and Chrome does the resampling as well, so the sample rate never has
 * to be touched here. */
/* The graph, the int16 conversion and the upload every 2 seconds are done by
 * web/capture.js (MimiCapture), which is shared with the extension. What is
 * left here is getting the stream and the on-screen notices. */
function stopCapture() {
  const c = state.capture;
  state.captureSession = null;
  if (!c) return;
  state.capture = null;
  // Not awaited, and the state above is cleared first: every caller of this is
  // synchronous (a session ending, a new capture replacing this one), and the
  // screen has to show the capture gone at once. The flush carries the last
  // buffered seconds to the session they belong to while that happens -- if
  // the page is being unloaded it may not finish, which is still strictly
  // better than the unconditional loss stop() alone caused.
  MimiCapture.stopAndFlush(c);
}

/* Have the user pick a tab. Called **before the session is created**.
 *
 * There are two reasons. getDisplayMedia only opens right after a user
 * gesture, and that gesture is good for a few seconds only, so a server round
 * trip first can leave the dialog never appearing. And cancelling here has
 * nothing to clear away, because nothing has been created yet.
 *
 * What comes back is a MediaStream, or null if none was obtained. */
async function requestTabAudio() {
  let media;
  try {
    // video:true is required. Ask Chrome for audio alone and it does not
    // offer the tab choice at all. The video that comes back is thrown away
    // unused.
    media = await navigator.mediaDevices.getDisplayMedia({
      video: true, audio: true,
    });
  } catch (err) {
    if (err && err.name === "NotAllowedError") return null;   // the user cancelled
    jobError(t("capture.error.getAudio", { error: (err && err.message) || err }));
    return null;
  }
  if (!media.getAudioTracks().length) {
    media.getTracks().forEach(t => t.stop());
    jobError(t("capture.error.noAudio"));
    return null;
  }
  return media;
}

/* The notice for a tab session is written in the **player area** only.
 *
 * Putting the same words in the top bar as well showed them twice on one
 * screen. That bar is narrow (460px in the script window) and has to be kept
 * for telling the user what to press, so the explanation goes down into the
 * empty player area. In this flow that area stays a black rectangle anyway.
 *
 * Called both on start and on resume -- resume goes through attachLive, which
 * wipes this notice with clearPlayerError() on its way. */
function tabStageNotice(tail, tile = focusedTile()) {
  playerError(t("capture.stage.notice") + (tail ? " " + tail : ""), null, tile);
}

/* Renaming.
 *
 * Offered for tab audio only. A session received from a URL has its title
 * fetched by yt-dlp, and fetched again on resume, so a name fixed here would
 * only go back. */
function syncRenameButton() {
  const live = state.live;
  $("rename-live").hidden = !(live && live.source === "tab");
}

function renameLive() {
  const live = state.live;
  if (!live) return;
  const box = $("now-title");
  const cur = box.textContent.trim();
  const input = document.createElement("input");
  input.className = "title-edit";
  input.value = cur === t("capture.tabAudio") ? "" : cur;
  input.placeholder = t("capture.rename.placeholder");
  box.replaceWith(input);
  input.focus();
  input.select();

  let closed = false;
  const done = async (save) => {
    if (closed) return;          // blur and Enter overlap and come in twice
    closed = true;
    const text = input.value.trim();
    input.replaceWith(box);
    if (!save || !text || text === cur) return;
    const r = await (await fetch("/api/live/title", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: live.id, title: text }),
    })).json();
    if (r.error) { jobError(r.error); return; }
    setNowTitle(text);
    if (state.doc) state.doc.title = text;
    if (live.probe) live.probe.title = text;
    // Fix that row of the library along with it. Redrawing the whole library
    // has the temporary row of a session being received vanish and come back,
    // which flickers.
    const row = $("video-list").querySelector(
      `.video-row[data-value="${CSS.escape("live:" + live.id)}"]`);
    if (row) {
      row.dataset.title = text;
      const t = row.querySelector(".vt");
      if (t) t.textContent = text;
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); done(true); }
    if (e.key === "Escape") { e.preventDefault(); done(false); }
  });
  input.addEventListener("blur", () => done(true));
}

/* The name of what was shared -- if it can be used at all.
 *
 * **For a tab it cannot.** When Chrome captures a tab it puts an opaque
 * identifier in label, not the tab's title. This is a value actually seen:
 *
 *     web-contents-media-stream://8D6FD737C5BFC47DBCE78F63FA28FECB
 *
 * Using that as the title is worse than "Tab audio", so it is filtered out and
 * an empty string comes back. The name is written in "＋ Add", or fixed later
 * with "✎ Name".
 *
 * The function stays all the same. Picking a window or a screen gives that
 * one's name, and another Chromium build may hand over a real title. If one
 * comes, it is used. */
function tabTitleFrom(media) {
  const v = media.getVideoTracks()[0];
  const raw = ((v && v.label) || "").trim();
  if (!raw) return "";
  // screen:0:0, window:12:0, web-contents-media-stream://5/12 …
  if (/^[a-z][a-z-]*:(\/\/)?[\d:/]/i.test(raw)) return "";
  return raw.replace(/\s+[-–—]\s+(Google Chrome|Chromium|Chrome)$/i, "")
            .slice(0, 200);
}

/* Was a window or the whole screen picked instead of a tab? Whether the sound
 * comes with it differs by platform, but a tab's does everywhere. */
function isTabSurface(media) {
  const v = media.getVideoTracks()[0];
  const s = v && v.getSettings ? v.getSettings() : null;
  // If the settings cannot be read it is taken for a tab -- if it is not, there is no sound and it was caught above.
  return !s || !s.displaySurface || s.displaySurface === "browser";
}

/* Send the stream already obtained down into the session. */
async function pipeCapture(media, sessionId) {
  stopCapture();
  const cap = await MimiCapture.start({
    media,
    workletUrl: "/static/capture-worklet.js",
    post: (buf) => fetch("/api/ingest/" + encodeURIComponent(sessionId), {
      method: "POST", headers: { "Content-Type": "application/octet-stream" },
      body: buf,
    }).then(r => r.json()),
    // This is where it lands when the user presses Chrome's "Stop sharing".
    // The order matters -- stopLive empties the notice slot, so the notice is
    // written after it.
    onEnded: () => {
      stopLive();
      showLiveNotice(t("capture.notice.sharingEnded"));
    },
    // The session is gone (a server restart, say). The module has already let the sharing go.
    onError: (msg) => {
      state.capture = null;
      showLiveNotice(t("capture.notice.sessionEnded", { error: msg }));
    },
    onDropped: (s) => showLiveNotice(
      t("capture.notice.dropped", { n: Math.round(s) })),
  });
  state.capture = cap;
  state.captureSession = sessionId;     // which session's sound this is. Released only when that session ends

  // Check that sound is actually arriving. AudioContext's resume() is
  // sometimes blocked, and failing quietly there is indistinguishable, to the
  // user, from transcription being slow.
  setTimeout(() => {
    if (state.capture === cap && !cap.n && !cap.sent) {
      showLiveNotice(t("capture.notice.silent"));
    }
  }, 4000);
}

/* This is where "＋ Add" lands when the sound source is set to "Sound from
 * another tab in this browser". The target is a tab and not a URL, so there is
 * no probe and no yt-dlp. */
async function startTabCapture(title, lang) {
  // Grab the script window first. This flow has no video to attach, so there
  // is no reason to leave the user on the main screen, and once the sharing
  // dialog has been through, popups are blocked. That is the only reason this
  // has to come before requestTabAudio below.
  const pending = openPendingScriptWindow();
  state.scriptWin = pending;

  // The sharing dialog. Cancel here and nothing at all happens.
  const media = await requestTabAudio();
  if (!media) { if (pending) pending.close(); state.scriptWin = null; return; }
  stopLive();
  // Use the name if one was written. Left empty, the picked tab's title is fetched.
  const name = title || tabTitleFrom(media);
  const res = await (await fetch("/api/live/capture", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: name, lang, viewer_lang: $("viewer-lang").value, backend: state.backend,
      asr: state.asr, refine: state.refine, genre: currentGenre(),
      profile: document.querySelector('#add-form select[name="profile"]').value,
    }),
  })).json();
  if (res.error) {
    media.getTracks().forEach(t => t.stop());
    if (pending) pending.close();
    state.scriptWin = null;
    jobError(res.error);
    return;
  }

  const probe = { id: "", title: name || MW_I18N.t("capture.tabAudio"), is_live: true };
  const t = soloTile();
  bindLive(t, {
    id: res.id, store: MimiCues.create(), es: null, speakers: new Set(),
    url: "", lang, probe, asr: state.asr, backend: state.backend, source: "tab",
  }, {
    id: probe.id, title: probe.title, source_lang: lang || "",
    viewer_lang: $("viewer-lang").value, translated: false,
    backends_done: [state.backend], live: true,
  });
  t.src = { site: "none" };          // there is no video to attach
  showTileInPanels(t);
  addLiveToPicker(probe, res.id);
  await attachLive(t);
  await pipeCapture(media, res.id);
  tabStageNotice(isTabSurface(media) ? "" :
    // A window or the whole screen is captured too if the sound comes with
    // it. There is no telling what will be mixed in, though, so this just
    // points out that such a choice was made.
    //
    // `const t` below shadows the lookup for this whole function body, so the
    // long name is the one that works here.
    MW_I18N.t("capture.stage.windowShare"));
  aimScriptWindow(pending, res.id);
}

/* Turn the empty window already grabbed into the script window. If a blocked
 * popup meant none could be grabbed, the top bar says so -- pressing "⧉ Pop
 * out" is a fresh gesture, so it opens then. That one line is telling the user
 * what to press, so it stays in the bar. */
function aimScriptWindow(pending, sessionId) {
  if (pending && !pending.closed) {
    pending.location = `/?script=${encodeURIComponent("live:" + sessionId)}`;
    hideLiveNotice();
    return;
  }
  showLiveNotice(t("capture.notice.popupBlocked"));
}
