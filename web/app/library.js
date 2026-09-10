/* mimiwatch front end — the list of videos and streams down the left.
 *
 * One of the files web/app.js was split into by concern. They are all plain
 * <script>s, read in the order index.html writes them down, and they share one
 * global scope -- module syntax is avoided here for the same reason as in
 * overlay.js, which is shared with the extension. Everything they call in each
 * other is a function call made at run time, so the file order only has to put
 * main.js last. */

/* Called from a row of the list. The button used to sit in the header, where it
 * could only delete "the one currently open" -- so what you were looking at in
 * the list and what got deleted were two different things. */
async function deleteVideo(id, title) {
  if (!confirm(t("library.video.delete.confirm",
                 { title: (title || "").slice(0, 50) }))) return;
  const res = await (await fetch("/api/video/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  })).json();
  if (res.error) { alert(res.error); return; }
  // If what was deleted is what is on screen, open something else. Otherwise
  // only the list is redrawn and the screen is left alone.
  const open = state.doc && state.doc.id === id && !isLiveDoc();
  const list = await (await fetch("/api/videos")).json();
  if (open && !list.length) { location.reload(); return; }
  await refreshVideoList(open ? list[0].id : undefined);
}

/* Deletes a past stream. The server refuses a session that is still receiving
 * -- "Stop" comes first.
 *
 * There used to be no way to delete a session at all, so the list only ever
 * grew. Test sessions and failed sessions piled up until the real streams were
 * pushed past the limit. */
async function deleteSession(sid, title) {
  if (!confirm(t("library.session.delete.confirm",
                 { title: (title || "").slice(0, 50) }))) return;
  const res = await (await fetch("/api/live/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: sid }),
  })).json();
  if (res.error) { alert(res.error); return; }
  // If what was deleted is what is on screen, clear the screen. The subtitles are gone now.
  if (state.live && state.live.id === sid) {
    detachLive();
    state.doc = null; state.cues = []; state.idx = -1;
    buildScript();
    setNowTitle(null);
    if (overlay) overlay.clear();
  }
  await refreshVideoList();
}

/* The list is drawn as rows, not as a <select>.
 *
 * A select was enough while picking was all it did, but deleting and the state
 * badge have to sit in the same place, and a title must not be cut down to one
 * line. With the delete button off in the header it can only delete "the one
 * currently open", so what you see in the list and what gets deleted are two
 * different things. */
/* The button group at the right of a row. A stopped stream gets ▶ resume and
 * ⟳ transcribe the whole video, a VOD gets ⟳ re-transcribe, and anything
 * finished gets 🗑. A row still receiving gets nothing -- "Stop" comes first.
 * The first draw and every state change (updateSessionRow) use this same one. */
function rowActions({ value, session, title, stopped, deletable, videoId, st }) {
  const box = document.createElement("span");
  box.className = "vact";
  const add = (cls, text, tip, fn) => {
    const b = document.createElement("button");
    b.className = cls;
    b.title = tip;
    b.textContent = text;
    b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
    box.appendChild(b);
  };
  if (session && stopped) {
    const why = st ? stopReason(st) : "stopped";
    if (why !== "ended") {
      add("vres", "▶", t("library.action.resume.tip"), async () => {
        openFromList(value);
        await resumeSession(session, why);
      });
    }
    if (videoId) {
      add("vre", "⟳", t("library.action.whole.tip"), () =>
        openRetranscribe(`https://www.youtube.com/watch?v=${videoId}`,
                         (st && st.source_lang) || "", title));
    }
  } else if (!session) {
    // Use the stored URL. For a VOD that is not on YouTube (m3u8 and the like) no URL can be built from the id.
    const url = (st && st.url) || `https://www.youtube.com/watch?v=${value}`;
    add("vre", "⟳", t("library.action.retranscribe.tip"), () =>
      openRetranscribe(url, (st && st.source_lang) || "", title));
  }
  if (deletable) {
    add("vdel", "🗑", session ? t("library.action.delete.session.tip")
                             : t("library.action.delete.video.tip"), () => {
      if (session) deleteSession(session, title);
      else deleteVideo(value, title);
    });
  }
  return box;
}

function videoRow({ value, session, title, meta, live, stopped, deletable, videoId, st }) {
  const row = document.createElement("div");
  row.className = "video-row" + (live ? " live" : "") + (stopped ? " stopped" : "");
  row.dataset.value = value;
  if (session) row.dataset.session = session;
  row.dataset.title = title;
  // A member of a multiview bundle carries an ⊞ mark, and pressing it opens the whole bundle (openFromList).
  const group = (st && st.group) || "";
  row.dataset.group = group;
  if (group) row.classList.add("mv");

  // In a list of nothing but titles it does not come at a glance which stream
  // is which. We use the thumbnail YouTube hands out as it is -- the player is
  // embedded already, so the browser talks to Google either way.
  const th = document.createElement("img");
  th.className = "vth";
  th.alt = "";
  th.decoding = "async";
  // The player embed comes first. If a dozen-odd list pictures all rush the
  // same host, the embed ends up queueing behind them.
  th.fetchPriority = "low";
  // `loading="lazy"` is not used. This element gets its src before it is in
  // the DOM, and the browser then never works out when to lift the delay: one
  // picture out of 22 appeared and the rest hung there. At about 10KB apiece
  // there is nothing to win by putting them off either.
  if (videoId) th.src = `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/mqdefault.jpg`;
  else th.classList.add("blank");
  // Keep the space even when it does not load. Ragged row heights make the list harder to read.
  th.addEventListener("error", () => { th.removeAttribute("src"); th.classList.add("blank"); });
  row.appendChild(th);

  const body = document.createElement("div");
  const t = document.createElement("div");
  t.className = "vt";
  t.textContent = title;
  body.appendChild(t);
  if (meta) {
    const m = document.createElement("div");
    m.className = "vm";
    m.textContent = meta;
    body.appendChild(m);
  }
  row.appendChild(body);

  row.appendChild(rowActions({ value, session, title, stopped, deletable, videoId, st }));

  row.addEventListener("click", () => openFromList(value));
  // Dragged onto the player area, it attaches as a tile beside the stream being watched (dropRow in tiles.js).
  row.draggable = true;
  row.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/mimiwatch-row", value);
    e.dataTransfer.effectAllowed = "copy";
    setTimeout(() => $("player-wrap").classList.add("dragging"), 0);   // same reason as in tiles.js
  });
  row.addEventListener("dragend", () => $("player-wrap").classList.remove("dragging"));
  return row;
}

/* Fixes one session row of the list up with the state the server sent: the line
 * count, the state, the name. The list is not redrawn whole, so the row under
 * the pointer does not jump. */
function updateSessionRow(row, s) {
  const running = LIVE_RUNNING.includes(s.state);
  const m = row.querySelector(".vm");
  if (m) {
    const n = s.lines != null ? s.lines : (s.cues || 0);
    m.textContent = running ? t("library.row.lines", { n })
      : t("library.row.linesState", { n, state: LIVE_STATE[s.state] || s.state });
  }
  row.classList.toggle("stopped", !running);
  row.classList.remove("pending");
  const title = s.title || s.url || "";
  if (title && title !== row.dataset.title) {
    row.dataset.title = title;
    const t = row.querySelector(".vt");
    if (t) t.textContent = title;
    // In multiview every row of the bundle is lit, so the title up top changes
    // for the focused session only, not for any lit row.
    if (state.live && state.live.id === s.id && !document.querySelector(".title-edit")) setNowTitle(title);
  }
  // The button group depends on the state. A row that was receiving has to grow
  // ▶·⟳·🗑 once it ends, and lose them again once it resumes and is receiving.
  row.lastElementChild.replaceWith(rowActions({
    value: "live:" + s.id, session: s.id, title, stopped: !running,
    deletable: !running, videoId: s.video_id || "", st: s,
  }));
}

function openFromList(value) {
  const row = $("video-list").querySelector(`.video-row[data-value="${CSS.escape(value)}"]`);
  const sid = row && row.dataset.session;
  markVideoRow(value);
  if (sid) {
    // If it is already on screen as a tile, only move the focus. If it is a member of a bundle, open the whole bundle.
    const t = tileBySession(sid);
    if (t) { setFocus(t); return; }
    // The Script window reads one session only -- opening the whole bundle would open an SSE per tile nobody sees.
    if (row.dataset.group && !state.scriptOnly && (!state.mv || state.mv.id !== row.dataset.group)) {
      openMultiview(row.dataset.group).then(ok => { if (!ok) resumeLive(sid); });
      return;
    }
    resumeLive(sid);
    return;
  }
  if (state.live) {
    // Opening a VOD does not cut the live reception. The user did not press
    // "Stop", and a VOD in the list is already transcribed and translated, so
    // it puts no load on the stream side. Only the screen lets go (detach)
    // while the server keeps receiving -- pressing that stream's row in the
    // list again picks it up where it left off.
    //
    // Tab audio is the one exception. That stream is tied to this window and
    // breaks the moment something else is opened anyway, so we tell the server
    // it is over as well.
    if (state.live.source === "tab") stopLive();
    else detachLive();
  } else {
    // The temporary row of a finished stream is cleared away once something else is opened.
    dropLiveOption(value);
  }
  loadVideo(value);
}

function markVideoRow(value) {
  // In multiview every member row of the bundle lights up -- they are all on screen, after all.
  const gid = state.mv && state.mv.id;
  $("video-list").querySelectorAll(".video-row").forEach(r =>
    r.classList.toggle("on", r.dataset.value === value || (!!gid && r.dataset.group === gid)));
  const row = value && $("video-list").querySelector(
    `.video-row[data-value="${CSS.escape(value)}"]`);
  setNowTitle(row ? row.dataset.title : null);
}

/* Whole-library search. The box sits above the list, and while it holds text
 * the list is the matches instead of the titles. A match is one line of
 * subtitle (the source or its translation) with its moment, and pressing it
 * opens the owner and moves there. */
let searchActive = false;
let searchTimer = null;

function onLibrarySearchInput() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runLibrarySearch, 300);
}

async function runLibrarySearch() {
  const box = $("library-search");
  const list = $("video-list");
  const q = (box.value || "").trim();
  if (!q) {
    searchActive = false;
    refreshVideoList();
    return;
  }
  const res = await (await fetch("/api/search?q=" + encodeURIComponent(q))).json();
  if (($("library-search").value || "").trim() !== q) return;    // it moved on
  searchActive = true;
  list.textContent = "";
  (res.results || []).forEach(r => list.appendChild(searchRow(r)));
  if (!list.children.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = t("library.search.empty");
    list.appendChild(e);
  }
}

function searchRow(r) {
  const row = document.createElement("div");
  row.className = "video-row search";
  row.dataset.value = r.value;
  const th = document.createElement("img");
  th.className = "vth blank";
  th.alt = "";
  row.appendChild(th);
  const body = document.createElement("div");
  const tt = document.createElement("div");
  tt.className = "vt";
  tt.textContent = r.title || r.value;
  body.appendChild(tt);
  const m = document.createElement("div");
  m.className = "vm";
  // The server marks the matched word with «». When the match is in the
  // translation, the source snippet has no mark and the translation side is
  // the one to show.
  const s = (r.snip && r.snip.indexOf("«") >= 0) ? r.snip
          : (r.snip_tr && r.snip_tr.indexOf("«") >= 0) ? r.snip_tr
          : (r.text || "");
  m.textContent = (r.start ? fmt(r.start) + "  ·  " : "") + s;
  body.appendChild(m);
  row.appendChild(body);
  row.addEventListener("click", () => openSearchResult(r));
  return row;
}

async function openSearchResult(r) {
  const sid = r.value.startsWith("live:") ? r.value.slice(5) : null;
  if (sid) {
    const t = tileBySession(sid);
    if (t) setFocus(t);
    else await resumeLive(sid);
  } else {
    if (state.live) {
      // Same rule as openFromList: a VOD does not cut the reception.
      (state.live.source === "tab" ? stopLive() : detachLive());
    }
    await loadVideo(r.value);
  }
  if (r.start != null && state.player) {
    const p = state.player;
    // A currentTime the browser sets before it has the metadata is ignored
    // (the HTML spec says so), and loadVideo just issued load() with no
    // await in between -- so on a file whose metadata is still arriving, the
    // seek is queued as a one-shot on the first ready state. The YouTube
    // adapter queues on its own, the stub does nothing.
    if (p.video && p.video.readyState < 1)
      p.video.addEventListener("loadedmetadata", () => p.seekTo(r.start), { once: true });
    else p.seekTo(r.start);
  }
}

/* The bar up top is the place that answers "what am I watching right now". */
function setNowTitle(title) {
  const el = $("now-title");
  el.textContent = title || t("library.nowTitle.empty");
  el.classList.toggle("empty", !title);
  el.title = title || "";
}

/* The watched list -- the addresses the server polls, and starts receiving by
 * itself the moment one of them goes live. The rule for when it starts is the
 * server's (live.py, the watchers section); the list here only shows it, and
 * sets or unsets the will. It is hidden while empty, so the screen stays as it
 * was for someone who leaves nothing to be watched. */

async function refreshWatchList() {
  const res = await (await fetch("/api/watchers")).json();
  const section = $("watch-section");
  const list = $("watch-list");
  if (!section || !list) return;
  const ws = res.watchers || [];
  section.hidden = !ws.length;
  list.textContent = "";
  ws.forEach(w => list.appendChild(watchRow(w)));
}

function watchRow(w) {
  const row = document.createElement("div");
  row.className = "video-row watch" + (w.live ? " live" : "") + (w.enabled ? "" : " stopped");
  row.dataset.url = w.url;

  const body = document.createElement("div");
  const tt = document.createElement("div");
  tt.className = "vt";
  tt.textContent = w.name || w.url;
  body.appendChild(tt);
  const m = document.createElement("div");
  m.className = "vm";
  m.textContent = w.live ? t("library.watch.live") : t("library.watch.waiting");
  body.appendChild(m);
  row.appendChild(body);

  const box = document.createElement("span");
  box.className = "vact";
  // The will. The row's click starts the stream when it is live; the button
  // sets the will and stops the click from taking it with it.
  const tg = document.createElement("button");
  tg.className = "wtg" + (w.enabled ? " on" : "");
  tg.title = w.enabled ? t("library.watch.disable.tip") : t("library.watch.enable.tip");
  tg.textContent = "◉";
  tg.addEventListener("click", (e) => { e.stopPropagation(); toggleWatcher(w.url, !w.enabled); });
  box.appendChild(tg);
  const del = document.createElement("button");
  del.className = "vdel";
  del.title = t("library.watch.delete.tip");
  del.textContent = "🗑";
  del.addEventListener("click", (e) => { e.stopPropagation(); deleteWatcher(w); });
  box.appendChild(del);
  row.appendChild(box);

  row.addEventListener("click", () => {
    // A live one with the will set is started here; the server's auto-start
    // does the same when the screen is free, this is the hand on the wheel.
    if (w.live && w.enabled) startFromWatcher(w);
  });
  return row;
}

async function toggleWatcher(url, enabled) {
  const res = await (await fetch("/api/watchers/toggle", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, enabled }),
  })).json();
  if (res.error) { alert(res.error); return; }
  refreshWatchList();
}

async function deleteWatcher(w) {
  if (!confirm(t("library.watch.delete.confirm",
                 { title: (w.name || w.url).slice(0, 50) }))) return;
  const res = await (await fetch("/api/watchers/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: w.url }),
  })).json();
  if (res.error) { alert(res.error); return; }
  refreshWatchList();
}

function startFromWatcher(w) {
  // The start takes a probe to name the stream. The probe's fields the address
  // does not give are empty, and the server fills in what it can when the
  // session resolves it.
  startLive(w.url, "", { id: "", title: w.name || w.url,
                         site: "", channel: "", url: w.url });
}

function addWatcherClick() {
  const url = prompt(t("library.watch.add.prompt"));
  if (url == null) return;
  const u = url.trim();
  if (!u) return;
  const name = (prompt(t("library.watch.add.name"), "") || "").trim();
  (async () => {
    const res = await (await fetch("/api/watchers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: u, name }),
    })).json();
    if (res.error) { alert(res.error); return; }
    refreshWatchList();
  })();
}

async function refreshVideoList(selectId, pre) {
  // While the search box holds text, the list is the matches, not the titles.
  // A library change in that state is skipped: clearing the box redraws.
  if (searchActive) return;
  // At start-up it is handed what has already been fetched. init used to send
  // two requests and this function then sent the same two again.
  const [list, sessions] = pre || await Promise.all([
    fetch("/api/videos").then(r => r.json()),
    fetch("/api/live/sessions").then(r => r.json()),
  ]);
  const box = $("video-list");
  // In multiview every row of the bundle is lit, so the first lit row need not
  // be the focused one. We go by what is being watched (the focused session).
  const current = box.querySelector(".video-row.on");
  const keep = state.live ? "live:" + state.live.id : (current && current.dataset.value);
  box.textContent = "";

  // A live session has no cue file, so closing the tab used to take that
  // stream's subtitles with it, all of them. The server holds them now, so the
  // session goes into the list and can be opened again. A session that never
  // got a single line has nothing to show and is left out -- except while it is
  // receiving: a stream just started in another window must be visible before
  // its first subtitle.
  sessions.filter(s => s.cues || LIVE_RUNNING.includes(s.state)).forEach(s => {
    const running = LIVE_RUNNING.includes(s.state);
    box.appendChild(videoRow({
      value: "live:" + s.id, session: s.id,
      title: s.title || s.url, videoId: s.video_id || "",
      meta: running ? t("library.row.lines", { n: s.cues })
        : t("library.row.linesState", { n: s.cues, state: LIVE_STATE[s.state] || s.state }),
      // Only a finished stream can be deleted. One still receiving needs "Stop" first.
      live: true, stopped: !running, deletable: !running, st: s,
    }));
  });
  list.forEach(v => {
    // For a local file the probe does not know the duration (ffprobe is not a prerequisite). We use what the transcription measured.
    const secs = v.duration || v.audio_seconds;
    const mins = secs ? t("library.row.minutes", { n: Math.round(secs / 60) }) : "";
    box.appendChild(videoRow({
      value: v.id, title: v.title, videoId: v.source === "file" ? "" : v.id,
      meta: [v.source_lang + (v.translated ? `→${v.viewer_lang}` : ""), mins]
        .filter(Boolean).join("  ·  "),
      deletable: true, st: v,
    }));
  });
  if (!box.children.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = t("library.empty");
    box.appendChild(e);
  }

  if (selectId && list.some(v => v.id === selectId)) {
    markVideoRow(selectId);
    await loadVideo(selectId);
  } else if (keep) {
    markVideoRow(keep);
  }
}

/* The list redraws whenever the videos or the sessions change, but not when the
 * language does -- and an idle screen can sit on it for a long time. The empty
 * title says the same, so it is re-set from here too. */
MW_I18N.onChange(() => {
  const el = $("now-title");
  if (el && el.classList.contains("empty")) setNowTitle(null);
  if ($("video-list")) refreshVideoList();
});
