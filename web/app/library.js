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
    // "Take the whole thing in" needs an address for the recording of what was
    // watched live, and the id is only enough to build one on YouTube. On chzzk
    // the id is the channel's, so this offered
    // "youtube.com/watch?v=<chzzk channel>" -- nobody's video -- and the
    // broadcast address it was watched at is not a recording either: chzzk
    // publishes the rewind under a number of its own that nothing here knows.
    // So the button is left out rather than made to fail.
    if (videoId && (!st || !st.site || st.site === "youtube")) {
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

/* The picture for a row, or "" for a row that gets the grey placeholder.
 *
 * The id used to be poured straight into YouTube's thumbnail host. A chzzk
 * recording's id is "chzzk-15186552", so every one of those rows asked
 * i.ytimg.com for a video it has never heard of and took the 404 -- and the
 * recording's real thumbnail, which the probe had already brought back, went
 * unused. So a row that carries its own picture uses it, and the ytimg guess is
 * left for the ids that are shaped like a YouTube video id (eleven characters
 * of its own alphabet) rather than for everything that is not a local file. */
const YT_VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

function rowThumbUrl(thumbnail, videoId) {
  if (thumbnail) return thumbnail;
  if (videoId && YT_VIDEO_ID.test(videoId)) {
    return `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/mqdefault.jpg`;
  }
  return "";
}

function videoRow({ value, session, title, meta, live, stopped, deletable, videoId, thumbnail, st }) {
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
  // is which. For a YouTube row we use the thumbnail YouTube hands out as it is
  // -- the player is embedded already, so the browser talks to Google either
  // way -- and a row that brought its own picture uses that one (rowThumbUrl).
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
  const thumbUrl = rowThumbUrl(thumbnail, videoId);
  if (thumbUrl) th.src = thumbUrl;
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
    if (isPushedSource(state.live.source)) stopLive();
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
// The meaning toggle. Off is the full-text search exactly as it was; on, the
// same box asks semsearch.py instead. It is a per-viewer habit, so it lives in
// this browser rather than in backends.json.
const SEARCH_MODE_KEY = "mimiwatch.searchMode";
let searchMeaning = loadSearchMode();
// What the note under the box is drawn from: {unavailable} out of an answer,
// or the indexing pass's {running, done, total, error} out of an answer or the
// change feed. renderSearchNote is the one place that turns it into words.
let searchIndex = null;
let searchFeed = null;       // the last pass status the change feed sent
let searchFeedSeq = 0;       // bumped per feed event, to tell which word is newer
let searchPartial = false;   // the meaning rows on screen came from a half-built index
let searchRerunFor = null;   // the query the one automatic re-run was spent on

function onLibrarySearchInput() {
  clearTimeout(searchTimer);
  searchRerunFor = null;
  searchTimer = setTimeout(runLibrarySearch, 300);
}

async function runLibrarySearch() {
  const box = $("library-search");
  const list = $("video-list");
  const q = (box.value || "").trim();
  const meaning = searchMeaning;
  if (!q) {
    searchActive = false;
    searchIndex = null;
    renderSearchNote();
    refreshVideoList();
    return;
  }
  const seq = searchFeedSeq;
  let res;
  if (meaning) {
    // The rows this answer brings decide whether a re-run is owed, not the
    // ones still on screen -- otherwise a pass ending while it is on its way
    // spends the one re-run on a duplicate of this very request.
    searchPartial = false;
    // The first meaning search loads the embedding model. A model that fails
    // comes back as an index error the note shows; a request that fails outright
    // (the server went away) lands here -- the list should then say nothing came
    // back, not sit on the previous query.
    try {
      res = await (await fetch("/api/search?mode=meaning&q=" + encodeURIComponent(q))).json();
    } catch { res = { results: [] }; }
  } else {
    res = await (await fetch("/api/search?q=" + encodeURIComponent(q))).json();
  }
  if (($("library-search").value || "").trim() !== q) return;    // it moved on
  if (searchMeaning !== meaning) return;                          // or the toggle did
  searchActive = true;
  list.textContent = "";
  if (meaning) {
    // No «» here: a meaning hit is a whole line, not a matched word. The
    // translation, when there is one, goes under it as a second line.
    (res.results || []).forEach(r => list.appendChild(searchRow(r, r.text || "", r.tr_text)));
  } else {
    // The server marks the matched word with «». When the match is in the
    // translation, the source snippet has no mark and the translation side is
    // the one to show.
    (res.results || []).forEach(r => {
      const s = (r.snip && r.snip.indexOf("«") >= 0) ? r.snip
              : (r.snip_tr && r.snip_tr.indexOf("«") >= 0) ? r.snip_tr
              : (r.text || "");
      list.appendChild(searchRow(r, s));
    });
  }
  // "Nothing matches" is not true of a search that could not run at all; the
  // note says why instead. While the index is still being built the two stand
  // together: nothing matches *yet*, and the note says how far it has got.
  if (!list.children.length && !res.unavailable) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = t("library.search.empty");
    list.appendChild(e);
  }
  if (meaning) meaningAnswered(res, seq, q);
  else { searchIndex = null; renderSearchNote(); }
}

/* One row of either kind. `meta` is what follows the moment on the second
 * line; `tr` is a third, for a meaning hit that has a translation. */
function searchRow(r, meta, tr) {
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
  m.textContent = (r.start ? fmt(r.start) + "  ·  " : "") + meta;
  body.appendChild(m);
  if (tr) {
    const mt = document.createElement("div");
    mt.className = "vm";
    mt.textContent = tr;
    body.appendChild(mt);
  }
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
      (isPushedSource(state.live.source) ? stopLive() : detachLive());
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

/* The meaning half of the search. The server answers from whatever part of the
 * library is indexed so far and starts a pass over the rest in the background,
 * so a first search on a big library comes back thin while the pass is still
 * going. The note under the box says so, the change feed moves its count, and
 * when the pass ends the search is asked once more so the rows cover
 * everything. */
function loadSearchMode() {
  try { return localStorage.getItem(SEARCH_MODE_KEY) === "meaning"; }
  catch { return false; }     // private windows and blocked storage both land here
}

function paintSearchMode() {
  const b = $("library-search-mode");
  if (b) b.setAttribute("aria-pressed", searchMeaning ? "true" : "false");
}

function toggleSearchMode() {
  searchMeaning = !searchMeaning;
  try {
    if (searchMeaning) localStorage.setItem(SEARCH_MODE_KEY, "meaning");
    else localStorage.removeItem(SEARCH_MODE_KEY);
  } catch { /* not remembered, but the switch still holds for this page */ }
  paintSearchMode();
  searchIndex = null;
  searchRerunFor = null;
  renderSearchNote();
  clearTimeout(searchTimer);
  if (($("library-search").value || "").trim()) runLibrarySearch();
}

/* The note under the box. Precedence: cannot run at all, then still indexing,
 * then the pass died; otherwise it is hidden. Only while meaning mode is on and
 * the box holds text -- the full-text search has nothing to say here. */
function renderSearchNote() {
  const el = $("library-search-note");
  if (!el) return;
  const box = $("library-search");
  const ix = (searchMeaning && box && (box.value || "").trim()) ? searchIndex : null;
  let text = "", warn = false;
  if (ix && ix.unavailable) {
    text = t(ix.unavailable === "model" ? "library.search.need_model"
                                        : "library.search.need_packages");
    warn = true;
  } else if (ix && ix.running) {
    text = t("library.search.indexing", { done: ix.done || 0, total: ix.total || 0 });
  } else if (ix && ix.error) {
    text = t("library.search.index_error", { error: ix.error });
    warn = true;
  }
  el.textContent = text;
  el.hidden = !text;
  el.classList.toggle("warn", warn);
}

function meaningAnswered(res, seq, q) {
  if (res.unavailable) {
    searchIndex = { unavailable: res.unavailable };
    searchPartial = false;
  } else {
    const ix = res.index || null;
    // The answer's pass status is read as the request arrives, before the
    // query is embedded -- the first time, that includes loading the model --
    // so a short pass can end, and say so on the feed, before this answer
    // lands. Taking the answer's word there would leave the note on
    // "indexing" with no event left to come and clear it. So a feed event
    // that came in meanwhile wins, unless it is plainly about another pass:
    // the answer says a pass is running and the event's count differs, which
    // is the end of the pass before this one, read late.
    const fed = searchFeedSeq !== seq ? searchFeed : null;
    searchIndex = (fed && (!ix || !ix.running || fed.total === ix.total)) ? fed : ix;
    // The rows were ranked against what was indexed when they were asked for;
    // a pass running then, or since, leaves them short.
    searchPartial = !!((ix && ix.running) || (searchIndex && searchIndex.running));
  }
  renderSearchNote();
  rerunWhenIndexed(q);
}

/* Once per query, and only when the rows on screen were drawn from a
 * half-built index that has since finished. Not after a pass that died: the
 * next search starts a new pass, so re-running on every failed end would retry
 * a broken model in a loop. And not more than once: a live session keeps
 * adding lines, each search starts a small pass for them, and re-running on
 * each of their ends would poll for as long as the stream runs. */
function rerunWhenIndexed(q) {
  const ix = searchIndex;
  if (!searchPartial || !ix || ix.unavailable || ix.running || ix.error) return;
  if (searchRerunFor === q) return;
  searchRerunFor = q;
  clearTimeout(searchTimer);
  runLibrarySearch();
}

/* A {"type": "search"} event off the change feed (bus.js): about once a second
 * while a pass runs, and once when it ends. */
function onSearchIndexEvent(m) {
  searchFeed = { running: !!m.running, done: m.done || 0, total: m.total || 0,
                 error: m.error || null };
  searchFeedSeq++;
  const box = $("library-search");
  const q = box ? (box.value || "").trim() : "";
  if (!searchMeaning || !q) return;
  searchIndex = searchFeed;
  // A pass that started after the rows were drawn -- another window's search,
  // new lines from a live session -- leaves them short as well.
  if (searchFeed.running) searchPartial = true;
  renderSearchNote();
  rerunWhenIndexed(q);
}

/* The note can only move on notices, and a hidden tab stops getting them:
 * Chrome slows it and then freezes it, and a feed that broke drops whatever
 * was sent meanwhile. A pass that ended in that gap left the note on
 * "indexing 5696/28836" for good with the rows still short -- seen with the tab
 * in the background for the four and a half minutes a first pass takes. When
 * the tab comes back or the feed reattaches (bus.js), a note still saying a
 * pass runs is checked by asking once more; the answer carries the status. */
function refreshSearchIfStale() {
  const box = $("library-search");
  const q = box ? (box.value || "").trim() : "";
  if (searchMeaning && q && searchIndex && searchIndex.running) runLibrarySearch();
}

// The markup is already in place when this script runs (the <script> tags sit
// at the end of index.html), so the toggle is wired here rather than in
// main.js -- the search box's own listener is there.
(function bindLibrarySearchMode() {
  const b = $("library-search-mode");
  if (!b) return;
  paintSearchMode();
  b.addEventListener("click", toggleSearchMode);
})();

// The toggle's label and title are data-i18n, which setLang redraws on its own;
// the note is written here, so it is redrawn here.
MW_I18N.onChange(() => renderSearchNote());

/* The bar up top is the place that answers "what am I watching right now". */
function setNowTitle(title, userRenamed = false) {
  const el = $("now-title");
  el.textContent = title || t("library.nowTitle.empty");
  el.classList.toggle("empty", !title);
  // The mark is a sibling, not part of the title: the title clips with an
  // ellipsis and would eat it on exactly the names it is there for.
  $("now-renamed").hidden = !userRenamed;
  el.title = title || "";
}

/* The watched list -- the addresses the server polls, and starts receiving by
 * itself the moment one of them goes live. The rule for when it starts is the
 * server's (live.py, the watchers section); the list here only shows it, and
 * sets or unsets the will.
 *
 * The section used to hide itself while the list was empty, so that the screen
 * stayed as it was for someone who leaves nothing to be watched. That also hid
 * the ＋ Watch button, which is the only way to leave a first address -- the
 * whole feature was indistinguishable from one that does not exist. The head
 * now always stands and a quiet line takes the place of the rows.
 *
 * The head also carries what the poller is doing, because nothing else does: a
 * pass that finds no change writes no row and publishes nothing on the bus, so
 * a loop taking its passes and a thread that died read the same from here. */

const WATCH_STATUS_MS = 15000;   // the poll is every 30 s; asking twice as often is plenty
let watchStatusTimer = null;

async function refreshWatchList() {
  const res = await (await fetch("/api/watchers")).json();
  const section = $("watch-section");
  const list = $("watch-list");
  if (!section || !list) return;
  const ws = res.watchers || [];
  list.textContent = "";
  if (!ws.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = t("library.watch.empty");
    list.appendChild(e);
  }
  ws.forEach(w => list.appendChild(watchRow(w)));
  setWatchStatus(res.poller);
  // "12s ago" has to keep moving on its own, and the bus is silent on a pass
  // that changed nothing, so the head is re-asked on a clock. One clock: this
  // function is called on every notice and on every edit of the list, and
  // without the clear each of those left another timer running behind it.
  clearInterval(watchStatusTimer);
  watchStatusTimer = setInterval(pollWatchStatus, WATCH_STATUS_MS);
}

/* Only while the tab is looked at. Chrome throttles a background tab's timers
 * rather than stopping them, so without this a tab left open overnight would
 * go on asking the server for a line nobody is reading -- the same rule bus.js
 * follows when it waits for the tab to come back before re-reading. */
async function pollWatchStatus() {
  if (document.visibilityState !== "visible") return;
  try {
    const res = await (await fetch("/api/watchers")).json();
    setWatchStatus(res.poller);
  } catch (_) { /* the next tick asks again */ }
}

/* Three states, three sentences: no thread, a thread that has not finished a
 * pass yet, and the time the last pass finished. Absent `poller` (an older
 * server answering a newer screen) says nothing at all rather than guessing. */
function setWatchStatus(p) {
  const el = $("watch-status");
  if (!el) return;
  if (!p) { el.textContent = ""; return; }
  if (!p.running) { el.textContent = t("library.watch.stopped"); return; }
  if (p.last_pass == null) { el.textContent = t("library.watch.checking"); return; }
  const secs = Math.max(0, Math.round(Date.now() / 1000 - p.last_pass));
  el.textContent = secs < 90 ? t("library.watch.checked", { n: secs })
                             : t("library.watch.checked.min", { n: Math.round(secs / 60) });
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
      title: s.title || s.url, videoId: s.video_id || "", thumbnail: s.thumbnail || "",
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
      thumbnail: v.thumbnail || "",
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
