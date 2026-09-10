/* The mimiwatch screen — global changes the server pushes (sessions, videos,
 * jobs), taken in and applied to that part only.
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope.
 *
 * Live subtitles arrive over SSE per session, but everything else -- a new
 * session, a session's line count and state, a video that appeared once
 * transcription finished, a job running in another window -- only showed up
 * after a reload. A stream started from the extension never appeared on this
 * screen, and the library's "receiving" stayed there after the stream had
 * ended. One stream of the server's `/api/events` is held open, and depending
 * on the kind of notice either one row of the library is fixed or the library
 * is read again. On a break EventSource reattaches by itself, and what was
 * missed in between is filled in by reading the library once after it comes
 * back. */

let bus = null;
let busWasDown = false;
let busRotating = false;
let listTimer = null;
let listWaiters = [];

/* Re-reading the library is gathered up for a moment. A notice can arrive for
 * every single subtitle line, and there is no reason to send two requests and
 * redraw the library each time.
 *
 * Work that follows **after** the re-read finishes has to hang off the promise
 * this returns. A clock of its own drifts -- Chrome slows the timers of a
 * background tab, so "pick that row 700ms from now" really did come round
 * before "read the library 400ms from now" and picked a row that was not
 * there. */
function scheduleListRefresh(delay = 400) {
  if (state.scriptOnly) return Promise.resolve();
  return new Promise((resolve) => {
    listWaiters.push(resolve);
    clearTimeout(listTimer);
    listTimer = setTimeout(async () => {
      listTimer = null;
      const waiters = listWaiters;
      listWaiters = [];
      try { await refreshVideoList(); } catch (_) { /* the next notice tries again */ }
      waiters.forEach(w => w());
    }, delay);
  });
}

function connectBus() {
  if (bus || state.scriptOnly) return;
  bus = new EventSource("/api/events");
  bus.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "session") onSessionChanged(m);
    else if (m.type === "video") onVideoChanged(m);
    else if (m.type === "job") onJobChanged(m);
    // The server closing on a schedule (rotate) is not a break. Reattaching does not re-read the library.
    else if (m.type === "rotate") busRotating = true;
    // Model download progress, completion, failure. If the dialog is open its
    // row is fixed, and once everything needed is in place the notice bar at
    // the top comes down.
    else if (m.type === "model") onModelEvent(m);
    // Update: a new version found, and its download progress. update.js fixes the bar and the section.
    else if (m.type === "update") onUpdateEvent(m);
    // A multiview bundle appeared, or its focus or members changed (moved by another window or by the server).
    else if (m.type === "multiview") onMultiviewChanged(m);
    // The watched list changed, or the probe changed a watcher's live finding.
    // The list is short; it is re-read whole.
    else if (m.type === "watchers") refreshWatchList();
  };
  bus.onopen = () => {
    if (busWasDown) scheduleListRefresh(0);   // fill in what changed while it was down
    busWasDown = false;
  };
  bus.onerror = () => {
    if (busRotating) { busRotating = false; return; }
    busWasDown = true;
  };
  // Chrome slows a background tab's timers to once a minute and, left long
  // enough, freezes it entirely (to save memory). Notices can pile up or break
  // in the meantime, so when the tab becomes visible again the library is read
  // once to fill in what changed since -- watching YouTube in another tab and
  // coming back is the ordinary use of this screen.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleListRefresh(0);
  });
}

/* One session changed. If it is in the library only that row is fixed; if it
 * is not (just started from another window or from the extension) the library
 * is read again. If this screen is watching nothing, the newly started stream
 * is opened as it is -- the same rule as returning, on first open, to a stream
 * already being received. */
function onSessionChanged(m) {
  const box = $("video-list");
  const row = box.querySelector(`.video-row[data-session="${CSS.escape(m.id)}"]`);
  // If a tile on screen is watching this session, its bar is fixed too (line count, state, title).
  const tile = tileBySession(m.id);
  if (tile && !m.deleted) {
    if (m.title) tile.title = m.title;
    if (!tile.live.lastStatus || tile.live.lastStatus.lines !== m.lines) {
      tile.live.lastStatus = { ...(tile.live.lastStatus || {}), ...m };
    }
    updateTileBar(tile);
  }
  if (m.deleted) {
    if (row) row.remove();
    if (state.live && state.live.id === m.id) {
      detachLive();
      state.doc = null; state.cues = []; state.idx = -1;
      buildScript(); setNowTitle(null);
      if (overlay) overlay.clear();
    }
    if (!box.querySelector(".video-row")) scheduleListRefresh(0);   // the empty-library notice
    return;
  }
  if (row) {
    updateSessionRow(row, m);
    if (row.dataset.group !== (m.group || "")) scheduleListRefresh();   // the bundle marker changed
    return;
  }
  scheduleListRefresh().then(() => {
    if (state.doc || state.live || !LIVE_RUNNING.includes(m.state)) return;
    if (!box.querySelector(`.video-row[data-session="${CSS.escape(m.id)}"]`)) return;
    markVideoRow("live:" + m.id);
    resumeLive(m.id);
  });
}

/* A video (a recording) changed -- transcription finished, a translation was
 * attached, or it was deleted. The library is read again, and a screen with
 * that video open re-reads its cues. Only in read mode, though -- redrawing
 * the whole subtitle log while the editor or the picker is open throws away
 * what was being done. If this window is the one that ran that job (jobLocal)
 * its polling loop re-reads on its own, so it is not done here. */
function onVideoChanged(m) {
  scheduleListRefresh();
  if (!state.doc || isLiveDoc() || state.doc.id !== m.id) return;
  if (m.reason === "deleted") {
    state.doc = null; state.cues = []; state.idx = -1;
    buildScript(); setNowTitle(null);
    if (overlay) overlay.clear();
    return;
  }
  if (state.scriptMode !== "read") return;
  if (state.jobId && state.jobLocal) return;
  reloadCues().catch(() => {});
}

/* A multiview bundle changed. If it is not the bundle this screen is watching,
 * only the library is read again. If it is, focus and members are brought in
 * line with the server -- another window moved the focus, or a member ended
 * and the server moved it, or the bundle is gone. It is not posted back to the
 * server (post:false) -- that has the two windows throwing the focus back and
 * forth without end. */
async function onMultiviewChanged(m) {
  scheduleListRefresh();
  if (!state.mv || state.mv.id !== m.id) return;
  if (m.deleted) {
    // The bundle is gone (every member ended). The tiles that remain are left
    // alone and only the bundle is forgotten -- each tile has already had, or
    // will shortly have, its own session's end state.
    state.mv = null;
    syncMvControls();
    return;
  }
  state.mv.focus = m.focus;
  state.mv.members = m.members;
  // A tile whose member the server dropped (an ended session) is left alone -- the subtitle log has to stay.
  const t = tileBySession(m.focus);
  if (t && t !== focusedTile()) setFocus(t, { post: false });
}

/* Progress of a job started in another window (or in the extension). This
 * window's own is drawn by the polling loop, so it is skipped here. */
function onJobChanged(st) {
  if (state.jobId === st.id && state.jobLocal) return;
  renderForeignJob(st);
}
