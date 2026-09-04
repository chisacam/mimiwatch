/* The mimiwatch screen — tiles: the cells that share the player area (#player-wrap).
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope -- module
 * syntax is avoided for the same reason as in overlay.js, which is shared with
 * the extension. Everything they call on each other is a function call at run
 * time, so the file order only has to keep main.js last.
 *
 * One tile = a player (an adapter) + the subtitle overlay above it + a title
 * strip + (when it is not focused) a transparent cover that moves the focus
 * when clicked. There used to be one #player and one #overlay hard-wired in,
 * but multiview needs several of each. With one tile neither the strip nor the
 * cover is visible, so it is the same screen as before.
 *
 * The rest of the screen only ever looks at the "focused tile": `overlay` and
 * `state.player` are the focused tile's, and so are `state.live`, `state.doc`
 * and `state.cues`. When the focus moves, setFocus swaps those four out and
 * redraws the script panel. */

let _tileSeq = 0;

function focusedTile() {
  return state.tiles.find(t => t.id === state.focus) || state.tiles[0] || null;
}

function tileBySession(sid) {
  return state.tiles.find(t => t.live && t.live.id === sid) || null;
}

/* Boot. Makes tile 0 and starts the subtitle drawing clock **once** -- the
 * YouTube player's onReady used to start it, and with a player per tile that
 * makes as many clocks as there are tiles. */
function initTiles() {
  const t = makeTile();
  t.el.classList.add("focused");
  state.focus = t.id;
  overlay = t.overlay;
  applyLayout();
  setInterval(renderCue, 100);
}

function makeTile() {
  const el = $("tile-tpl").content.firstElementChild.cloneNode(true);
  const tile = {
    id: "t" + (++_tileSeq), el,
    playerEl: el.querySelector(".tile-player"),
    overlay: null, adapter: null,
    src: null,          // the result of srcOf(). What is playing
    live: null,         // the live session this tile is watching (same shape as state.live)
    doc: null,          // the document of what this tile is watching (same shape as state.doc)
    title: "",
  };
  tile.overlay = MimiOverlay.attach({ overlay: el.querySelector(".overlay"), box: () => el });
  // Saved on every drop. There is one subtitle position, and it applies to the focused tile.
  tile.overlay.onPos = (p) => { state.cuePos = p; persist(); };
  if (state.cuePos) tile.overlay.setPos(state.cuePos);
  const cover = el.querySelector(".tile-cover"), bar = el.querySelector(".tile-bar");
  cover.addEventListener("click", () => setFocus(tile));
  bar.addEventListener("click", () => setFocus(tile));
  el.querySelector(".tile-close").addEventListener("click", (e) => {
    e.stopPropagation();
    removeTile(tile);
  });
  // Drag to swap places. The handles are the cover (unfocused tiles) and the strip (every tile).
  for (const h of [cover, bar]) {
    h.draggable = true;
    h.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/mimiwatch-tile", tile.id);
      e.dataTransfer.effectAllowed = "move";
      // The cover is transparent, so dragging it gives a transparent drag image. Use the strip (the title) as the image.
      try { e.dataTransfer.setDragImage(bar, 12, 12); } catch (_) { /* unsupported: the default image */ }
      // The catching film goes up **on the next tick**. Change the element under
      // the cursor inside dragstart and Chrome cancels the drag then and there
      // -- which is why dragging a tile did nothing at all.
      setTimeout(() => $("player-wrap").classList.add("dragging"), 0);
    });
    h.addEventListener("dragend", () => $("player-wrap").classList.remove("dragging"));
  }
  const drop = el.querySelector(".tile-drop");
  drop.addEventListener("dragover", (e) => {
    if (!dragHasOurs(e)) return;
    e.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    $("player-wrap").classList.remove("dragging");
    const tid = e.dataTransfer.getData("text/mimiwatch-tile");
    const row = e.dataTransfer.getData("text/mimiwatch-row");
    if (tid) swapTiles(state.tiles.find(t => t.id === tid), tile);
    else if (row) dropRow(row);
  });
  // Movement over the cover calls the fullscreen controls too (movement over the iframe never reaches us).
  el.addEventListener("mousemove", showFsControls);
  $("player-wrap").insertBefore(el, $("fs-controls"));
  state.tiles.push(tile);
  return tile;
}

function dragHasOurs(e) {
  const types = [...(e.dataTransfer.types || [])];
  return types.includes("text/mimiwatch-tile") || types.includes("text/mimiwatch-row");
}

/* Swaps two tiles' places. The order of state.tiles is the placement, and on
 * screen they move by CSS `order` alone. **The DOM nodes must not be moved** --
 * an iframe reloads the moment it is moved in the DOM, so the player goes back
 * to its initial state (a play button, from the beginning), and the IFrame API
 * object points at a player that is gone, leaving it loading forever. That is
 * what actually happened. In 1+2/1+3 the focus takes the big area, so what
 * changes there is the order of the small cells. */
function swapTiles(a, b) {
  if (!a || !b || a === b) return;
  const i = state.tiles.indexOf(a), j = state.tiles.indexOf(b);
  state.tiles[i] = b;
  state.tiles[j] = a;
  syncTileOrder();
  syncMvControls();
  requestAnimationFrame(applyCueSize);
}

/* The order of state.tiles into grid placement. The grid's auto-placement follows `order`, not DOM order. */
function syncTileOrder() {
  state.tiles.forEach((t, i) => { t.el.style.order = String(i); });
}

/* The door the single-source path goes through. Collapses the tiles to one and
 * hands it back -- opening a recording, starting a new live stream, opening a
 * different broadcast from the list. Sessions the other tiles were watching are
 * only detached from the screen; the server keeps them (the same sense as
 * detachLive). */
function soloTile() {
  collapseTiles();
  return state.tiles[0];
}

function collapseTiles() {
  const keep = state.tiles[0];
  for (const t of state.tiles.slice(1)) {
    detachTile(t);
    if (t.adapter) t.adapter.destroy();
    t.overlay.destroy();
    t.el.remove();
  }
  state.tiles.length = 1;
  state.mv = null;
  if (state.focus !== keep.id) {
    keep.el.classList.add("focused");
    state.focus = keep.id;
    overlay = keep.overlay;
    state.player = keep.adapter;
    if (keep.adapter) keep.adapter.setMuted(false);
  }
  applyLayout();
}

/* Lets go of the session. The player is left alone -- the same rule as "Stop",
 * which stops only the subtitles and keeps the broadcast playing. */
function detachTile(tile) {
  if (tile.live && tile.live.es) tile.live.es.close();
  tile.live = null;
  tile.doc = null;
  tile.overlay.clear();
  updateTileBar(tile);
}

/* Moves the focus. The sound, the subtitles and the subtitle log on the right all follow. */
function setFocus(tile, opts = {}) {
  if (!tile) return;
  // focusedTile()'s "the first tile if there is none" fallback must not be used
  // here. Right after the focused tile is closed, state.focus points at a tile
  // that is gone, and the fallback is the surviving tile, so this looked like
  // "already focused" and did nothing -- the bug where clicking the last tile
  // never gave it the focus.
  const prev = state.tiles.find(t => t.id === state.focus) || null;
  if (prev === tile && tile.el.classList.contains("focused")) return;
  if (prev && prev !== tile) {
    if (prev.adapter) prev.adapter.setMuted(true);
    prev.overlay.clear();
    prev.el.classList.remove("focused");
  }
  tile.el.classList.add("focused");
  // Unmute and start it playing too. Moving the focus is a user gesture (a click
  // or a key), so playback is not blocked, and Twitch sometimes stalls the
  // moment it is unmuted.
  if (tile.adapter) { tile.adapter.setMuted(false); tile.adapter.playVideo(); }
  state.focus = tile.id;
  overlay = tile.overlay;
  state.player = tile.adapter;
  if (state.cuePos) overlay.setPos(state.cuePos);
  showTileInPanels(tile);
  if (tile.live) markVideoRow("live:" + tile.live.id);
  syncMvControls();
  requestAnimationFrame(applyCueSize);
  if (opts.post !== false && state.mv && tile.live) mvFocus(state.mv.id, tile.live.id);
  persist();
}

/* Seats a player in a tile. Whatever was already there is cleared away. */
async function mountTile(tile, src, opts = {}) {
  if (state.scriptOnly) return;
  clearPlayerError(tile);
  if (tile.adapter) tile.adapter.destroy();
  tile.playerEl.textContent = "";
  tile.src = src;
  tile.adapter = adapterFor(src);
  if (tile === focusedTile()) state.player = tile.adapter;
  updateTileBar(tile);
  try {
    // The last stage of the buffering watch: rebuild this tile's player from scratch (the same as a reload).
    tile.adapter._remount = (wasMuted, autoplay) => {
      if (tile.adapter && tile.adapter.kind === "youtube") {
        // The focused tile keeps its sound on (first pass: autoplay, second: a play button), the others autoplay muted.
        mountTile(tile, src, { muted: tile !== focusedTile() || wasMuted, autoplay: !!autoplay });
      }
    };
    await tile.adapter.mount(tile.playerEl, src, {
      muted: !!opts.muted, autoplay: !!opts.autoplay,
      live: !!(tile.live || (tile.doc && tile.doc.live)),
      onError: (msg, vid) => playerError(msg, vid, tile),
    });
  } catch (err) {
    playerError((err && err.message) || String(err), null, tile);
  }
}

/* The tile strip: site, title, state. With one tile the CSS hides the strip. */
const SITE_MARK = { youtube: "▶", twitch: "◉", hls: "≋", media: "▤", none: "" };

function updateTileBar(tile) {
  const live = tile.live;
  const title = (tile.doc && tile.doc.title) || tile.title || "";
  tile.el.querySelector(".tile-title").textContent = title;
  tile.el.querySelector(".tile-site").textContent = SITE_MARK[(tile.src || {}).site] || "";
  let st = "";
  if (live) {
    const m = live.lastStatus || {};
    const running = !live.state || LIVE_RUNNING.includes(live.state);
    st = running ? t("tile.state.lines", { n: m.lines || live.store.size() })
                 : (LIVE_STATE[live.state] || live.state);
  }
  tile.el.querySelector(".tile-state").textContent = st;
  tile.el.classList.toggle("stopped", !!(live && live.state && !LIVE_RUNNING.includes(live.state)));
}

/* ---------- layouts ----------
 *
 * Every tile count has a default layout, and what the user picked is kept in
 * prefs. In 1+2 and 1+3 the focused tile takes the big area -- the CSS seats it
 * there by .focused, so moving the focus leaves the DOM as it is. */
const LAYOUTS = { "2": [2, 2], "2h": [2, 2], "1p2": [3, 3], "1p3": [4, 4], "2x2": [3, 4] };   // name → [min, max] tile count
const DEFAULT_LAYOUT = { 1: "1", 2: "2", 3: "1p2", 4: "2x2" };

function layoutFits(name, n) {
  const r = LAYOUTS[name];
  return !!r && n >= r[0] && n <= r[1];
}

function applyLayout(name) {
  window.__tilesChangedAt = Date.now();     // the YouTube adapter's buffering watch reads "just after a relayout" briefly
  const n = state.tiles.length;
  if (name && layoutFits(name, n)) {
    state.mvLayout = name;
  }
  const use = n <= 1 ? "1" : (layoutFits(state.mvLayout, n) ? state.mvLayout : DEFAULT_LAYOUT[n] || "2x2");
  const wrap = $("player-wrap");
  syncTileOrder();
  // className is not replaced wholesale -- fullscreen's fs-active hangs on the same element.
  const prevUse = ([...wrap.classList].find(c => c.startsWith("mv-")) || "").slice(3);
  [...wrap.classList].filter(c => c.startsWith("mv-")).forEach(c => wrap.classList.remove(c));
  wrap.classList.add("mv-" + use);
  if (prevUse && prevUse !== use) preemptRelayoutStall();
  document.querySelectorAll("[data-layout]").forEach(b => {
    b.classList.toggle("on", b.dataset.layout === use);
    b.disabled = !layoutFits(b.dataset.layout, n);
  });
  syncMvControls();
  requestAnimationFrame(applyCueSize);
}

/* When a layout change resizes the focused tile, some browsers leave an unmuted
 * YouTube player stuck buffering even though it holds the data (it happens
 * inside the embed, so it cannot be stopped from outside). On a browser that
 * has been through such a stall once (prefs.ytRelayoutStall), the focused
 * player is rebuilt immediately instead of waiting out the 3s spinner -- one or
 * two seconds of black beats a 3s spinner. Browsers that never saw it are left
 * alone. */
function preemptRelayoutStall() {
  if (!loadPrefs().ytRelayoutStall) return;
  const t = focusedTile();
  if (!t || !t.live || !t.adapter || t.adapter.kind !== "youtube" || !t.adapter.ready || !t.adapter._remount) return;
  console.warn("[yt] reordering -- rebuilding the focused player up front (this browser has stalled after a reorder before)");
  requestAnimationFrame(() => t.adapter._remount(false, true));
}

/* Showing and hiding the multiview controls. "Add tile" when the focus is live,
 * the layout buttons when there are two or more tiles. */
function syncMvControls() {
  const n = state.tiles.length;
  const group = $("mv-group");
  if (group) group.hidden = n < 2;
  const add = $("mv-add");
  if (add) add.hidden = state.scriptOnly || !isLiveDoc() || n >= 4;
  const fsRow = $("fs-mv");
  if (fsRow) {
    fsRow.hidden = n < 2;
    fsRow.querySelectorAll("[data-focus-tile]").forEach(b => {
      const t = state.tiles[+b.dataset.focusTile];
      b.hidden = !t;
      if (t) {
        b.classList.toggle("on", t === focusedTile());
        b.title = (t.doc && t.doc.title) || t.title || "";
      }
    });
  }
}

/* Closing a tile = stopping that session and taking it out of the group. The
 * last tile is never closed -- that is "Stop"'s job. */
async function removeTile(tile) {
  if (state.tiles.length <= 1) return;
  const wasFocus = tile === focusedTile();
  if (state.mv && tile.live) await mvRemove(state.mv.id, tile.live.id);
  detachTile(tile);
  if (tile.adapter) tile.adapter.destroy();
  tile.overlay.destroy();
  tile.el.remove();
  state.tiles.splice(state.tiles.indexOf(tile), 1);
  if (wasFocus) {
    // Empties the globals that pointed at the tile that is gone and gives the focus to the first surviving tile.
    state.focus = null;
    state.live = null; state.doc = null; state.player = null; overlay = null;
    setFocus(state.tiles[0], { post: false });
  }
  if (state.tiles.length === 1) state.mv = null;
  applyLayout();
}

/* ---------- the server's group API ----------
 *
 * The shape of it lives in these four functions and nowhere else. */
async function mvPost(path, body) {
  return (await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })).json();
}

function mvFocus(gid, sid) {
  return mvPost("/api/multiview/focus", { group: gid, id: sid }).catch(() => ({}));
}

function mvRemove(gid, sid) {
  return mvPost("/api/multiview/remove", { group: gid, id: sid }).catch(() => ({}));
}

function mvCreate(body) {
  return mvPost("/api/multiview", body);
}

function mvAdd(gid, body) {
  return mvPost("/api/multiview/add", { group: gid, ...body });
}

/* ---------- creating and restoring a multiview ----------
 *
 * "Add tile" attaches one **next to** the broadcast being watched. The first
 * attach creates the group (taking the session already being watched into it,
 * and the new source as a waiting session); after that they are added to the
 * group. The sound and the subtitles stay with the focus (what was already
 * being watched). */
function liveStartArgs(lang) {
  return {
    lang, viewer_lang: $("viewer-lang").value, backend: state.backend,
    asr: state.asr, refine: state.refine, genre: currentGenre(),
    profile: document.querySelector('#add-form select[name="profile"]').value,
  };
}

async function addTile(url, lang, probe) {
  const cur = focusedTile();
  if (!cur || !cur.live) { jobError(t("tile.error.needLive")); return; }
  if (state.tiles.length >= 4) { jobError(t("tile.error.tooMany")); return; }
  const args = liveStartArgs(lang);
  let res, members;
  if (!state.mv) {
    res = await mvCreate({ sessions: [cur.live.id], sources: [{ url }], focus: cur.live.id, ...args });
    if (res.error) { jobError(res.error); return; }
    state.mv = { id: res.id, focus: res.focus, members: res.members.map(m => m.id) };
    members = res.members;
  } else {
    res = await mvAdd(state.mv.id, { url, ...args });
    if (res.error) { jobError(res.error); return; }
    // The server's multiview notification may have arrived first and put it in already.
    if (!state.mv.members.includes(res.id)) state.mv.members.push(res.id);
    members = [res];
  }
  // A new session comes back before yt-dlp has answered, so site and video_id
  // are empty. Fill them from what probe just worked out and seat the player
  // right away.
  await mountMembers(members, { url, site: probe.site, video_id: probe.id,
                                channel: probe.channel, title: probe.title || url });
}

/* Turns the members the server returned that have no tile yet into tiles. `fill` is what to put in where the state has a gap. */
async function mountMembers(members, fill = {}) {
  for (const m of members) {
    if (tileBySession(m.id)) continue;
    const t = makeTile();
    const st = { ...m };
    for (const k of ["url", "site", "video_id", "channel", "title"]) if (!st[k] && fill[k]) st[k] = fill[k];
    await openSessionInTile(t, st);
    await attachLive(t);
  }
  applyLayout();
}

/* A list row was dragged onto the player area. If it is a live session it is
 * attached as a tile next to the broadcast being watched -- one still being
 * received is taken in as it is, and a stopped one is resumed by the server as
 * the same session and put in as a waiting tile. If nothing is being watched it
 * is simply opened. */
async function dropRow(value) {
  const row = $("video-list").querySelector(`.video-row[data-value="${CSS.escape(value)}"]`);
  const sid = row && row.dataset.session;
  if (!row) return;
  if (!sid) { jobError(t("tile.error.vodNotAllowed")); return; }
  const have = tileBySession(sid);
  if (have) { setFocus(have); return; }
  const cur = focusedTile();
  if (!cur || !cur.live) { openFromList(value); return; }
  if (state.tiles.length >= 4) { jobError(t("tile.error.tooMany")); return; }
  const args = liveStartArgs(null);
  let res, members;
  if (!state.mv) {
    res = await mvCreate({ sessions: [cur.live.id, sid], focus: cur.live.id, ...args });
    if (res.error) { jobError(res.error); return; }
    state.mv = { id: res.id, focus: res.focus, members: res.members.map(m => m.id) };
    members = res.members;
  } else {
    res = await mvAdd(state.mv.id, { session: sid, ...args });
    if (res.error) { jobError(res.error); return; }
    if (!state.mv.members.includes(res.id)) state.mv.members.push(res.id);
    members = [res];
  }
  await mountMembers(members, { title: row.dataset.title });
}

/* The group the server holds, put on screen as it is. On a reload, or when a member is clicked in the list. */
async function openMultiview(gid) {
  const g = await (await fetch(`/api/multiview/${encodeURIComponent(gid)}`)).json();
  if (!g || g.error) return false;
  const first = soloTile();
  detachTile(first);
  state.mv = { id: g.id, focus: g.focus, members: g.members.map(m => m.id) };
  let i = 0;
  for (const m of g.members) {
    const t = i === 0 ? first : makeTile();
    i++;
    await openSessionInTile(t, m);
  }
  const focus = tileBySession(g.focus) || state.tiles[0];
  // The focus has to be settled before feeding them, so each tile is seated with its own sound state (muted).
  if (focus !== focusedTile()) {
    focusedTile().el.classList.remove("focused");
    focus.el.classList.add("focused");
    state.focus = focus.id;
    overlay = focus.overlay;
    if (state.cuePos) overlay.setPos(state.cuePos);
  }
  for (const t of state.tiles) await attachLive(t);
  showTileInPanels(focus);
  if (focus.live) markVideoRow("live:" + focus.live.id);
  applyLayout();
  return true;
}

/* A tile strip is rewritten on every cue while the stream is being received,
 * but a stopped tile keeps the strip it last drew -- switching the language
 * would leave that one in the old language. */
MW_I18N.onChange(() => { state.tiles.forEach(tile => updateTileBar(tile)); });
