/* The mimiwatch screen — live sessions: starting, resuming, receiving SSE, stopping.
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope -- module
 * syntax is avoided for the same reason as in overlay.js, which is shared with
 * the extension. Everything they call on each other is a function call at run
 * time, so the file order only has to keep main.js last. */

function hideLiveNotice() {
  const box = $("live-notice");
  box.hidden = true;
  box.textContent = "";
}

/* Joins a broken session back on as the same session. Cues are stored by session
 * id, so the script up to that point stays where it is and the rest is joined
 * on behind it. */
/* `why` is why it stopped. The server dying and the sharing ending are different
 * things, and what has to be done to start again differs too -- tab audio has
 * to be handed over by the browser again. */
/* The notice strip for a stopped session. The wording differs by why it stopped,
 * and what can be done about it is attached as buttons -- "Resume", and, if
 * there is a video id, "Transcribe the whole video".
 *
 * A session the user stopped themselves (`stopped`) can be resumed too. It used
 * not to be offered, on the grounds that asking back is a nuisance, but with
 * live streams the user often pauses and comes back, and with no way to join on
 * then the subtitles split across two sessions. The one exception is a
 * broadcast that has already ended (`stopped_by: ended`) -- there is nothing to
 * resume then, and instead the whole video left behind as a recording can be
 * transcribed. */
function offerResume(sessionId, why, st) {
  const box = $("live-notice");
  box.textContent = {
    tab: t("live.resume.tab"),
    mic: t("live.resume.mic"),
    error: t("live.resume.error"),
    interrupted: t("live.resume.interrupted"),
    stopped: t("live.resume.stopped"),
    ended: t("live.resume.ended"),
  }[why] || t("live.resume.unknown");
  if (why !== "ended") {
    const b = document.createElement("button");
    b.className = "seg";
    b.textContent = t("live.resume.button");
    b.onclick = () => resumeSession(sessionId, why, b);
    box.appendChild(b);
  }
  const vid = st && st.video_id;
  if (vid) {
    const r = document.createElement("button");
    r.className = "seg";
    r.textContent = t("live.retranscribe.button");
    r.title = t("live.retranscribe.hint");
    r.onclick = () => openRetranscribe(`https://www.youtube.com/watch?v=${vid}`,
                                       st.source_lang || "", st.title || "");
    box.appendChild(r);
  }
  box.hidden = false;
}

/* Joins a broken session back on **as the same session**. The notice strip's
 * button and the list row's ▶ both come here. `btn` is the button that was
 * pressed (if there is one, progress is written into it). */
async function resumeSession(sessionId, why, btn) {
  if (btn) { btn.disabled = true; btn.textContent = t("live.resume.busy"); }
  // The script window is claimed here. This is the only point where this click
  // is still alive -- once the share window below has been picked, Chrome blocks
  // window.open. The same reason and the same order as at the start.
  const pending = isPushedSource(why) ? openPendingScriptWindow() : null;
  if (pending) state.scriptWin = pending;

  // Resumes with the engine the picker points at right now. If it was changed
  // under "Manage" while it was stopped, that one is used from the new stretch
  // onwards.
  const res = await (await fetch("/api/live/resume", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: sessionId, asr: state.asr, backend: state.backend }),
  })).json();
  if (res.error) {
    if (pending) { pending.close(); state.scriptWin = null; }
    showLiveNotice(t("live.resume.failed", { error: res.error }));
    if (btn) { btn.disabled = false; btn.textContent = t("live.resume.button"); }
    return;
  }
  hideLiveNotice();
  // It is the same session, so all that is needed is attaching again. detachLive
  // cuts what is attached now, and opening it fresh is what receives the state
  // events from the beginning.
  detachLive();
  state.live = null;
  await resumeLive(sessionId);
  await refreshVideoList();
  // The server cannot rewind audio the browser pushed. It only continues once
  // the browser hands it over again -- with the session alive but no sound
  // arriving, it stays "receiving" and never gains a line.
  //
  // This test was `res.source !== "tab"`, which a mic session passes: resume
  // would have returned here and left a live session that never receives
  // anything, with nothing on screen saying why.
  if (!isPushedSource(res.source)) return;
  const mic = res.source === "mic";
  const media = mic ? await requestMicAudio() : await requestTabAudio();
  if (!media) {
    if (pending) { pending.close(); state.scriptWin = null; }
    // The session is alive again but no sound is arriving. Without writing that
    // state onto the screen there is no way to tell why it says "receiving" and
    // never gains a line.
    tabStageNotice(t(mic ? "live.mic.reshare" : "live.tab.reshare"));
    showLiveNotice(t(mic ? "live.mic.reshareLong" : "live.tab.reshareLong"));
    return;
  }
  await pipeCapture(media, sessionId);
  tabStageNotice();
  aimScriptWindow(pending, sessionId);
}

/* Why it stopped, as the notice strip's string key. `resumeLive` and the list rows use the same rule. */
function stopReason(st) {
  // The source doubles as the reason key for a pushed session: what ended is
  // the share or the microphone, and those need different words.
  if (isPushedSource(st.source)) return st.source;
  if (st.stopped_by === "ended") return "ended";
  if (st.state === "interrupted") return "interrupted";
  if (st.state === "error" || st.stopped_by === "stream") return "error";
  return "stopped";
}

/* Transcribes a recording again -- with the same engine too, if you like.
 *
 * Opens the "Add video" dialog with the URL filled in. Pick the transcription
 * engine, the language and the genre there, press "Start", and the server
 * transcribes a video it already has again (the translations and hand edits of
 * lines whose text is unchanged are carried over). The only way to transcribe
 * again used to be pasting the same URL into this dialog once more, so there was
 * no telling it was even possible. */
function openRetranscribe(url, lang, title) {
  const f = $("add-form");
  renderAsrPicker();
  fillEngineSelect(f.querySelector('select[name="backend"]'), state.backends, state.backend, LOCKED.tr);
  f.source.value = "url";
  setAddSource("url");
  f.url.value = url;
  if ([...f.lang.options].some(o => o.value === (lang || ""))) f.lang.value = lang || "";
  const h = $("add-dialog").querySelector("h3");
  h.textContent = title ? t("live.retranscribe.title", { title: title.slice(0, 40) })
                        : t("live.retranscribe.titlePlain");
  $("add-dialog").showModal();
}

function showLiveNotice(text) {
  const box = $("live-notice");
  box.textContent = text;
  box.hidden = false;
}

/* Swaps a running session's transcription engine in place.
 *
 * It used to take pressing "Restart with the new engine". That changed the
 * session id, and cues are stored by session id, so **the whole script up to
 * that point disappeared.** The translator was already being swapped inside the
 * session, so there was no reason for the transcriber alone to behave that
 * way. */
async function askLiveRestart() {
  const stale = state.live && state.live.state
                && !LIVE_RUNNING.includes(state.live.state);
  // There is nothing to change on a stopped session -- the next resume starts
  // with the new engine. The notice strip must not be cleared here: changing the
  // engine used to make the "Resume" button disappear on the spot.
  if (stale) return;
  if (!state.live || state.live.asr === state.asr) {
    hideLiveNotice();
    return;
  }
  const res = await (await fetch("/api/live/asr", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: state.live.id, asr: state.asr }),
  })).json();
  if (res.error) {
    // On failure the server keeps using the engine it had. The picker on screen
    // has to be put back too, or the two disagree.
    setAsr(state.live.asr);
    showLiveNotice(t("live.asr.switchFailed", { error: res.error }));
    return;
  }
  state.live.asr = state.asr;
  hideLiveNotice();
}

async function startLive(url, lang, probe) {
  stopLive();
  const res = await (await fetch("/api/live/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url, lang, viewer_lang: $("viewer-lang").value, backend: state.backend,
      asr: state.asr, refine: state.refine, genre: currentGenre(),
      profile: document.querySelector('#add-form select[name="profile"]').value,
      record: state.record, record_video: state.recordVideo,
    }),
  })).json();
  if (res.error) { jobError(res.error); return; }

  // A live session has no cue file: cues arrive over SSE and accumulate in
  // place, and `t` is media seconds -- the same axis the YouTube player
  // reports for a live stream, so the usual lookup still applies.
  const t = soloTile();
  bindLive(t, {
    id: res.id, store: MimiCues.create(), es: null, speakers: new Set(),
    url, lang, probe, asr: state.asr, backend: state.backend, source: res.source || "hls",
    channelKey: liveChannelKey(probe),
  }, {
    id: probe.id, title: probe.title, source_lang: lang || "",
    viewer_lang: $("viewer-lang").value, translated: false,
    backends_done: [state.backend], live: true,
  });
  t.src = srcOf({ site: probe.site, video_id: probe.id, channel: probe.channel,
                  play_url: probe.play_url, url });
  showTileInPanels(t);
  addLiveToPicker(probe, res.id);
  await attachLive(t);
}

/* Reopens a session that already exists -- the tab was reloaded, or the server
 * restarted so reception broke off but the cues written down are still there.
 * The server hands the cues straight back right after the SSE connection, so
 * all that is needed here is the same vessel as starting a live stream fresh,
 * and the rest rides the same event path. It is watched in a single tile --
 * opening it as one cell of a multiview is openSessionInTile. */
async function resumeLive(sessionId) {
  if (state.live && state.live.id === sessionId) return;   // already being watched
  const st = await (await fetch(`/api/live/status/${sessionId}`)).json();
  if (!st.id) { jobError(st.error || MW_I18N.t("live.error.sessionNotFound")); return; }
  const t = soloTile();
  detachTile(t);
  await openSessionInTile(t, st);
  showTileInPanels(t);
  await attachLive(t);
  // A tab session has no video to seat. attachLive clears the notice left by
  // whatever was watched before on its way through, so this flow's notice is
  // written again after it.
  if (isPushedSource(st.source)) {
    const running = LIVE_RUNNING.includes(st.state);
    tabStageNotice(running ? "" : MW_I18N.t("live.tab.logOnly"), t);
  }
}

/* Ties one session to a tile (the SSE is not opened yet -- attachLive does
 * that). `st` is the answer from /api/live/status, or a multiview member's
 * state. */
async function openSessionInTile(tile, st) {
  const running = LIVE_RUNNING.includes(st.state);
  bindLive(tile, {
    id: st.id, store: MimiCues.create(), es: null, speakers: new Set(),
    url: st.url, lang: st.source_lang || null, state: st.state,
    probe: { id: st.video_id, title: st.title },
    source: st.source || "hls",
    asr: st.asr_backend || "", backend: st.backend || "",
    channelKey: liveChannelKey(st),
    lastStatus: running ? null : { ...st, type: "status" },
  }, {
    id: st.video_id || "", title: st.title || st.url,
    title_by_user: !!st.title_by_user,
    source_lang: st.source_lang || "", viewer_lang: st.viewer_lang,
    translated: false, backends_done: [st.backend], live: true,
  });
  tile.src = await seatable(st);
  tile.title = st.title || st.url || "";
  updateTileBar(tile);
}

/* What to seat in the tile for this session.
 *
 * A site with no embed (chzzk) is played from its own manifest, and that URL
 * carries a token that expires, so it is not kept with the session -- a stored
 * one would reach the player as an address that answers 403. A session the
 * server has just come back to, or one that has been sitting stopped, therefore
 * arrives without one and `srcOf` has nothing to give. Asking for a fresh one
 * costs a yt-dlp call on the server and happens only here, seating a tile.
 *
 * Tab audio is skipped: the picture is the user's own tab and there is nothing
 * to resolve. */
async function seatable(st) {
  let src = srcOf(st);
  if (src.site !== "none" || st.source === "tab" || !st.url) return src;
  try {
    const res = await (await fetch("/api/live/playurl", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: st.id }),
    })).json();
    if (res && res.url) { st.play_url = res.url; src = srcOf(st); }
  } catch (_) { /* no picture; the tile is left empty rather than showing another */ }
  return src;
}

/* Puts a session and a document into a tile. On the focused tile, the screen's globals (state.live and the rest) go with it. */
function bindLive(tile, live, doc) {
  if (tile.live && tile.live.es && tile.live !== live) tile.live.es.close();
  tile.live = live;
  tile.doc = doc;
  tile.title = doc.title || "";
  if (tile === focusedTile()) {
    state.live = live;
    state.doc = doc;
    state.cues = live.store.cues;     // the array the store edits in place
    state.idx = -1;
  }
}

/* Reflects the focused tile's state into the subtitle log on the right, the top
 * bar and the controls. On a start, on a reopen, and when the focus moves --
 * all three pass through the same tail. */
function showTileInPanels(tile) {
  const live = tile.live;
  state.live = live;
  state.doc = tile.doc;
  state.cues = live ? live.store.cues : ((tile.doc && tile.doc.cues) || []);
  state.idx = -1;
  if (live) {
    // Line up with the translation backend that was in use then. The stored
    // translations were made by that backend, so filing them under the backend
    // picked right now would show work as done that was never done.
    if (live.backend && state.backends.some(b => b.id === live.backend)) {
      state.backend = live.backend;
    }
    // The same goes for the transcription engine. If what the session actually
    // uses and what the picker points at differ, then whatever is changed next
    // leaves the screen and the server out of step.
    if (live.asr && state.asrBackends.some(b => b.id === live.asr)) setAsr(live.asr);
  }
  buildScript();
  renderBackendPicker();
  applyModeForDoc();
  syncRenameButton();
  $("job").hidden = true;
  state.jobId = null;
  $("live-badge").hidden = !isLiveReceiving();
  // Tab audio has no video to line up against, so the offset means nothing either.
  $("offset-wrap").style.display = !live ? "" : (isPushedSource(live.source) ? "none" : "flex");
  setNowTitle(tile.doc ? tile.doc.title : null,
              !!(tile.doc && tile.doc.title_by_user));
  hideLiveNotice();
  if (live) renderLiveStatus(tile);
  else updateLangStatus();
  syncMvControls();
  // The document belongs to the transcript, not to the screen. Moving the
  // focus to another tile has to drop the one on screen and ask for that
  // tile's -- otherwise one talk's document stands over another's video, and
  // it looks like a document rather than like a mistake.
  state.outline = null;
  syncDocViewButton();
  if (state.docView) {
    renderDocView();
    loadOutline(outlineOwner());
  }
  pinScriptToBottom();
}

async function attachLive(tile) {
  const live = tile.live;
  // The notice box left behind by whatever was watched before is cleared here.
  clearPlayerError(tile);
  const src = tile.src || { site: "none" };
  // Seated even when there is nothing to seat. Skipping the call left the tile
  // holding whatever was in it before, so opening a chzzk broadcast the server
  // had no manifest for showed the last YouTube player watched, still playing.
  // mountTile takes the old one out and puts the do-nothing adapter in, which is
  // what a session with no picture of its own (tab audio) wanted all along.
  await mountTile(tile, src, { muted: tile !== focusedTile() });
  const es = new EventSource(`/api/live/events/${live.id}`);
  live.es = es;
  es.onmessage = (ev) => {
    if (tile.live !== live) return;          // the tile came to be watching something else meanwhile
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "cue") onLiveCue(m, tile);
    else if (m.type === "translation") onLiveTranslation(m, tile);
    else if (m.type === "status") onLiveStatus(m, tile);
    // The server closes the stream deliberately every 4.5 minutes (because of
    // the extension service worker's 5-minute rule). The onerror that follows
    // shortly is not a break, so "Live connection lost" is not raised.
    else if (m.type === "rotate") live.rotating = true;
    // A line was deleted in another window. The main window and the script
    // window are watching the same session, so an edit in one has to reach the
    // other.
    else if (m.type === "outline") onOutlineEvent(m, tile);
    else if (m.type === "drop") dropCue(m.id, tile);
    // The subtitles were thrown away on the server. Every screen keeps its own
    // copy of the list, so each has to be told.
    else if (m.type === "clear") clearCues(tile);
  };
  es.onerror = () => {
    // For a finished session the server sends the whole backlog and then closes
    // the stream. That is a normal ending, not a break, and EventSource
    // reconnects by itself when cut, so not closing here means receiving every
    // subtitle again every few seconds. A running session, the other way round,
    // needs that automatic reconnection, so it is left alone.
    const st = live.state;
    if (st && !LIVE_RUNNING.includes(st)) { es.close(); return; }
    if (live.rotating) { live.rotating = false; return; }
    if (tile === focusedTile()) $("lang-status").innerHTML = t("live.status.disconnected");
  };
  // The channel's offset, when one was set down for it before. A VOD has no
  // such thing, so this is a no-op everywhere but a live stream.
  if (tile === focusedTile()) applyChannelOffset();
}

function addLiveToPicker(probe, sessionId) {
  // The picker was still naming whichever recording was open, while the
  // screen showed a broadcast. A live session is not a saved video, so it
  // gets a temporary entry that lasts as long as the broadcast is on screen.
  // Dropping every earlier live entry first is also what keeps re-adding the
  // same broadcast from stacking a second row on top of a stopped one.
  const box = $("video-list");
  box.querySelectorAll(".video-row.live.pending").forEach(r => r.remove());
  const empty = box.querySelector(".empty");
  if (empty) empty.remove();
  // The value is keyed by session id. Start the same broadcast twice and the
  // video ids collide, which is where picking one video later would land on the
  // session entry instead.
  const value = "live:" + sessionId;
  const row = videoRow({
    value, session: sessionId, title: probe.title || "",
    videoId: probe.id || "", meta: t("live.picker.receiving"), live: true, deletable: false,
  });
  row.classList.add("pending");     // a temporary entry, until the next reload
  box.prepend(row);
  markVideoRow(value);
}

/* Removing the entry on stop was the mismatch: the player went on showing a
 * broadcast the list no longer had. Keep the entry, say the subtitles ended. */
function markLiveStopped(sid) {
  const box = $("video-list");
  const row = sid ? box.querySelector(`.video-row[data-session="${CSS.escape(sid)}"]`)
                  : box.querySelector(".video-row.live.pending");
  if (!row) return;
  row.classList.add("stopped");
  const m = row.querySelector(".vm");
  if (m) m.textContent = t("live.row.stopped");
}

/* The stopped entry stands for what the player is showing. Once the viewer
 * picks something else the player moves on, and so does the entry. */
function dropLiveOption(keepValue) {
  $("video-list").querySelectorAll(".video-row.live.pending").forEach(r => {
    if (r.dataset.value !== keepValue) r.remove();
  });
}

/* One subtitle line. On a tile that is not focused only the store and the strip
 * are updated -- the subtitle log on screen belongs to the focused tile, and
 * when the focus arrives it is redrawn from that store (showTileInPanels). */
function onLiveCue(m, tile = focusedTile()) {
  const live = tile && tile.live;
  if (!live) return;

  // The rules for reflecting this into the list (the same id replaces, replaces
  // takes lines out) belong to cuestore.js, which is shared with the extension.
  // All that happens here is clearing away the rows of the lines that dropped.
  const r = live.store.upsert(m);
  const cue = r.cue;
  const before = live.speakers.size;
  if (cue.speaker) live.speakers.add(cue.speaker);
  if (tile !== focusedTile()) {
    // A line written in another window may not have the latest time. The array alone is put back in order.
    if (r.isNew) resortCue(cue, live.store.cues);
    updateTileBar(tile);
    return;
  }
  r.removed.forEach(id => {
    const row = $("script").querySelector(`.line[data-id="${id}"]`);
    if (row) row.remove();
  });
  state.idx = -1;
  if (before < 2 && live.speakers.size >= 2) {
    // The chips just became meaningful; the lines already on screen need them.
    buildLiveScript();
  } else {
    appendScriptLine(cue);
    // For a line written in another window (cue/add), arrival order and time order differ.
    if (r.isNew) resortCue(cue, live.store.cues);
  }
  renderCue();
  updateTileBar(tile);
}

function buildLiveScript() {
  $("script").textContent = "";
  state.cues.forEach(c => appendScriptLine(c));
}

function onLiveTranslation(m, tile = focusedTile()) {
  const live = tile && tile.live;
  if (!live) return;
  // Filed under that session's translation backend. When the focus arrives
  // state.backend is lined up with it too (showTileInPanels), so the screen
  // reads the same slot.
  const cue = live.store.translate(m.id, live.backend || state.backend, m.text);
  if (!cue) return;
  if (tile.doc && !tile.doc.translated) {
    tile.doc.translated = true;
    if (tile === focusedTile()) applyModeForDoc();
  }
  if (tile !== focusedTile()) return;
  const row = $("script").querySelector(`.line[data-id="${cue.id}"]`);
  if (row) refreshScriptRow(row, cue);
  // A line of translation was attached and this row grew taller. Take hold of the bottom again.
  pinScriptToBottom();
  renderCue();
}

/* A session state arrived. What has to be remembered (state, title, engine) is
 * written for any tile, the side effects (stopping the tab capture, marking the
 * list) only for that session, and the text on screen only when this is the
 * focused tile. */
function onLiveStatus(m, tile = focusedTile()) {
  const live = tile && tile.live;
  if (!live) return;
  live.state = m.state;
  live.lastStatus = m;
  // The "switching the video saving" notice comes down here, and only here.
  // `attached` counts the ffmpegs the session has stood up, so a value other
  // than the one remembered when the button was pressed means the new one is
  // reading -- which is the thing the notice is about, and the only honest
  // signal that it has happened. A session that gave up instead reports its own
  // error, and that is what should be on screen rather than a notice saying the
  // reception is on its way back. Hidden only while this tile is the focused
  // one, because the strip is a single band shared by every tile.
  if (live.awaitAttach != null
      && (m.attached !== live.awaitAttach || m.state === "error"
          || m.state === "stopped")) {
    live.awaitAttach = null;
    if (tile === focusedTile()) hideLiveNotice();
  }
  if (m.asr_backend) live.asr = m.asr_backend;
  if (m.backend) live.backend = m.backend;
  if (tile.doc) tile.doc.title_by_user = !!m.title_by_user;
  // Follows a change of name. Editing it with "✎ Name" makes the server send
  // the state again, so what was edited in the main window reaches the script
  // window by the same path.
  if (m.title && tile.doc && m.title !== tile.doc.title
      && !document.querySelector(".title-edit")) {
    tile.doc.title = m.title;
    tile.title = m.title;
    if (tile === focusedTile()) setNowTitle(m.title, !!m.title_by_user);
  }
  if (m.state === "error" || m.state === "stopped") {
    // stopLive() used to be called here. It empties state.live, and two things
    // fell over because of that.
    //
    //   - SSE sends the state **first** and the accumulated cues behind it. So
    //     if state.live is empty on the way past this line, the backlog that
    //     arrives right afterwards is thrown away wholesale by onLiveCue's first
    //     line (`if (!live) return`). Opening a session with 312 lines already
    //     received showed one line of error and nothing else.
    //   - hideLiveNotice() wiped out the "Resume" that had just been raised.
    //
    // An error is no reason to forget the session. What was written down is
    // real, and an error is exactly where you want to try again. So it is
    // handled the same as "interrupted" -- write down why it stopped, and stop
    // only the receiving. The tab capture is let go **only when it is this
    // session's** -- another multiview tile may be uploading tab audio.
    if (state.captureSession === live.id) stopCapture();
    markLiveStopped(live.id);
  }
  updateTileBar(tile);
  if (tile === focusedTile()) renderLiveStatus(tile);
}

/* What the session is doing with the audio and the video, as a piece of the
 * status line.
 *
 * A failed write has to reach the screen. The session goes on transcribing
 * after one (live.py `_record` and `_video_failed` report the error and let
 * everything else run), so subtitles keep arriving and nothing else on the page
 * would look any different from a session that is saving -- and a recording
 * that stopped with nobody told is the exact failure that saving exists to
 * prevent. The two fail separately even though one ffmpeg now feeds both, so
 * both are asked: the WAV is written here in Python off the pipe and the mp4 is
 * an output of that ffmpeg, so either can stop with the other still going, and
 * whichever it was has to say so on its own line. */
function recordingNote(m) {
  const bits = [];
  if (m.recording_error)
    bits.push(t("live.status.recordFailed", { error: esc(m.recording_error) }));
  else if (m.recording)
    bits.push(t("live.status.recording", { n: Math.round(m.recording_s || 0) }));
  if (m.recording_video_error)
    bits.push(t("live.status.recordVideoFailed", { error: esc(m.recording_video_error) }));
  else if (m.recording_video)
    bits.push(t("live.status.recordingVideo",
                { mb: Math.round((m.recording_video_bytes || 0) / 1048576) }));
  return bits.map(b => " · " + b).join("");
}

/* The two saving switches, as part of the same status line that reports what
 * they are doing.
 *
 * They are here rather than in the add form because the form is asked before
 * the broadcast has started, and a broadcast is the one thing where that is
 * the wrong moment to decide: what makes it worth keeping usually happens
 * after it is under way. The form keeps its boxes -- they are the answer for
 * a broadcast you already know you want -- and this is the answer for the one
 * you did not.
 *
 * Only while the session is receiving. A stopped session has nothing to switch;
 * what it offers instead is "Resume", which carries both flags back.
 *
 * The video switch is left out for a pushed source, the same way the form hides
 * its box: the extension and the capture page upload sound and nothing else, so
 * there is no picture to save.
 *
 * They go at the **head** of the line rather than beside the recording note
 * they belong to, because .bar clips at the right and .status cuts with an
 * ellipsis: a status reading "Receiving · source ja → ko · transcription
 * sensevoice · 312 lines · saving 1200s · video 350 MB" is already wider than
 * the slot on a laptop, and anything past the cut cannot be clicked at all.
 * What is cut has to be the prose, not the only thing on the line that does
 * something. */
function recordingToggles(m) {
  if (!LIVE_RUNNING.includes(m.state)) return "";
  const one = (field, on, label, hint) =>
    `<button type="button" class="rec-toggle${on ? " on" : ""}"`
    + ` data-record="${field}" data-on="${on ? "1" : "0"}"`
    + ` title="${esc(t(hint))}" aria-pressed="${on ? "true" : "false"}">`
    + `${esc(t(label))}</button>`;
  let out = one("record", !!m.record, "live.record.audio",
                "live.record.audioHint");
  if (!isPushedSource(m.source))
    out += one("record_video", !!m.record_video, "live.record.video",
               "live.record.videoHint");
  return out + " ";
}

/* Switches one of them on the session being watched.
 *
 * The answer is written into `lastStatus` and the line drawn again rather than
 * waiting for the status event the server sends: that event arrives over SSE a
 * moment later and would leave the button looking unpressed in between, which
 * reads as a click that did nothing. The event redraws the same thing when it
 * lands. */
async function onRecordToggle(e) {
  const btn = e.target.closest(".rec-toggle");
  if (!btn) return;
  const tile = focusedTile();
  const live = tile && tile.live;
  if (!live || !live.lastStatus) return;
  const field = btn.dataset.record;
  const want = btn.dataset.on !== "1";
  btn.disabled = true;
  // Said before the answer comes back, not after. The switch costs a reconnect
  // of a few seconds and the answer is what arrives at the end of it, so a
  // notice written afterwards would appear once reception was already back.
  // What is remembered with it is how many ffmpegs the session had stood up at
  // the moment of the press; `onLiveStatus` takes the notice down when that
  // number changes. The reconnect happens on the server after the route has
  // already answered, so the answer itself cannot say the reception is back.
  if (field === "record_video") {
    live.awaitAttach = live.lastStatus.attached ?? 0;
    showLiveNotice(t("live.record.videoSwitching"));
  }
  let res;
  try {
    res = await (await fetch("/api/live/record", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: live.id, [field]: want }),
    })).json();
  } catch (err) {
    res = { error: String(err) };
  }
  btn.disabled = false;
  if (res.error) {
    // What the server kept doing is what it was doing before, so nothing on
    // screen changes except the reason. Nothing is being waited for either.
    live.awaitAttach = null;
    showLiveNotice(t("live.record.switchFailed", { error: res.error }));
    return;
  }
  if (!res.reconnect) {
    // Nothing was stood up again, so there is no signal coming and the strip
    // must not be left carrying one. The audio half never interrupts anything,
    // and the video half answers `reconnect: false` when there was no ffmpeg to
    // end -- the session is still resolving, or the reading thread is between
    // processes, and the change lands on the next spawn with no break at all.
    live.awaitAttach = null;
    hideLiveNotice();
  }
  live.lastStatus = { ...live.lastStatus, record: res.record,
                      record_video: res.record_video };
  if (tile === focusedTile()) renderLiveStatus(tile);
}

/* Writes the focused tile's session state into the top bar (#lang-status), the
 * badge and the notice strip. When a state event arrives and when the focus
 * comes to this tile -- the two have to write the same words. */
function renderLiveStatus(tile) {
  const live = tile.live;
  const el = $("lang-status");
  if (!live) return;
  $("live-badge").hidden = !isLiveReceiving();
  const m = live.lastStatus;
  if (!m) { el.className = "status"; el.textContent = ""; return; }
  if (m.state === "error") {
    el.className = "status warn";
    el.textContent = m.error || t("live.status.error");
    if (isPushedSource(m.source) || m.url) offerResume(m.id, stopReason(m), m);
    return;
  }
  // An interrupted session is not an error. Reception broke off, but the cues
  // standing here really were written down, so the session is not folded away;
  // only why it stopped is announced.
  if (m.state === "interrupted") {
    el.className = "status warn";
    el.innerHTML = t("live.status.interrupted",
                     { state: LIVE_STATE.interrupted, n: m.lines || 0 })
      + recordingNote(m);
    offerResume(m.id, stopReason(m), m);
    return;
  }
  if (m.state === "stopped") {
    // "Stop" was pressed in another window or in the extension. Only the
    // receiving stopped, so the cues stay and a way to resume is put in the
    // strip. A stopped session can be resumed whatever the reason it stopped --
    // only a broadcast that has ended has nothing to resume, and there we offer
    // transcribing the whole video instead.
    if (isPushedSource(m.source) || m.url) offerResume(m.id, stopReason(m), m);
  }
  // The whole line goes amber when saving failed -- sound or picture -- even
  // though the session itself is fine. `.status.warn b` is what colours the
  // bold runs, so the class has to sit here rather than on the fragment.
  el.className = (m.recording_error || m.recording_video_error)
    ? "status warn" : "status";
  const src = m.source_lang || "auto";
  const eng = (m.asr || "").replace(/-Q8_0$|\.gguf$/g, "");
  el.innerHTML = recordingToggles(m)
    + t("live.status.headline", { state: LIVE_STATE[m.state] || m.state,
                                  src, viewer: m.viewer_lang })
    + (eng ? " · " + t("live.status.engine", { engine: esc(eng) }) : "")
    + (m.lines ? " · " + t("live.status.lines", { n: m.lines }) : "")
    + (m.focused === false ? " · " + t("live.status.standby") : "")
    + recordingNote(m);
}

/* "Stop" ends the transcription session, not the viewing. Nothing here
 * touches the player: the viewer asked for the subtitles to stop, not for the
 * broadcast to. The cues already received stay in the panel and in
 * state.mode -- they cost nothing and re-reading them is the whole point of
 * the panel. */
/* Lets go on the screen only. The server session is left alone, so the writing down carries on. */
function detachLive() {
  const live = state.live;
  if (!live) return;
  // The channel's offset sits under its own key now. Put the slider back on
  // the global player setting, so what the live adjustment made is not
  // carried into a VOD (a VOD aligns itself and needs no offset).
  const p = loadPrefs();
  if (p.offset != null) {
    $("offset").value = p.offset;
    state.offset = p.offset;
    $("offset-val").textContent = state.offset.toFixed(1) + "s";
  }
  const t = tileBySession(live.id);
  if (t) detachTile(t);
  else if (live.es) live.es.close();
  state.live = null;
  hideLiveNotice();
  syncRenameButton();
  $("live-badge").hidden = true;
  $("offset-wrap").style.display = "";
}

function toggleLiveMenu(open) {
  const menu = $("live-menu"), btn = $("live-menu-btn");
  menu.hidden = !open;
  btn.classList.toggle("on", open);
  btn.setAttribute("aria-expanded", String(!!open));
  if (!open) return;
  const r = btn.getBoundingClientRect();
  menu.style.top = `${Math.round(r.bottom + 4)}px`;
  menu.style.left = "auto";
  menu.style.right = `${Math.round(window.innerWidth - r.right)}px`;
}

async function clearLiveCues() {
  const live = state.live;
  if (!live) return;
  toggleLiveMenu(false);
  if (!confirm(t("live.clearCues.confirm"))) return;
  const res = await fetch("/api/cue/clear", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session: live.id }),
  }).then(r => r.json());
  if (res.error) { alert(res.error); return; }
  // The session will broadcast its cleared state via the bus
}

function stopLive() {
  // A tab share can outlive the session. Not let go together with the session,
  // Chrome's "sharing" indicator stays up and audio nobody reads keeps being
  // uploaded.
  const live = state.live;
  if (!live || state.captureSession === live.id) stopCapture();
  if (!live) return;
  if (live.es) live.es.close();
  hideLiveNotice();
  fetch("/api/live/stop", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: live.id }),
  }).catch(() => {});
  // The tile is left alone (the broadcast keeps playing). Only the session is forgotten.
  const t = tileBySession(live.id);
  if (t) { t.live = null; updateTileBar(t); }
  state.live = null;
  syncRenameButton();
  $("live-badge").hidden = true;
  $("offset-wrap").style.display = "";
  markLiveStopped(live.id);
  const el = $("lang-status");
  el.className = "status";
  el.textContent = MW_I18N.t("live.status.stopped");
}

/* The status line and the notice band are written when a status event arrives,
 * which for a stopped session may have been minutes ago -- switching the
 * language would leave both of them sitting in the old one. */
MW_I18N.onChange(() => {
  const tile = focusedTile();
  if (tile && tile.live) renderLiveStatus(tile);
});
