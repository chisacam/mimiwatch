/* mimiwatch 화면 — 타일: 플레이어 자리(#player-wrap)를 나눠 쓰는 칸.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다.
 *
 * 타일 하나 = 플레이어(어댑터) + 그 위의 자막 오버레이 + 제목 띠 + (초점이
 * 아닐 때) 누르면 초점을 옮기는 투명한 덮개. 예전에는 #player 와 #overlay 가
 * 하나씩 박혀 있었는데, 멀티뷰는 그것을 여러 벌 두어야 합니다. 타일이 하나일
 * 때는 띠도 덮개도 보이지 않아 예전 화면과 같습니다.
 *
 * 화면의 나머지는 「초점 타일」만 봅니다: `overlay` 와 `state.player` 는 초점
 * 타일의 것이고, `state.live`/`state.doc`/`state.cues` 도 그렇습니다. 초점이
 * 옮겨 가면 setFocus 가 그 넷을 갈아 끼우고 스크립트 패널을 다시 그립니다. */

let _tileSeq = 0;

function focusedTile() {
  return state.tiles.find(t => t.id === state.focus) || state.tiles[0] || null;
}

function tileBySession(sid) {
  return state.tiles.find(t => t.live && t.live.id === sid) || null;
}

/* 부팅. 타일 0 을 만들고 자막 그리기 시계를 **한 번만** 겁니다 -- 예전에는
 * 유튜브 플레이어의 onReady 가 걸었는데, 타일마다 플레이어가 생기면 시계도
 * 그만큼 생깁니다. */
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
    src: null,          // srcOf() 의 결과. 무엇을 틀고 있는가
    live: null,         // 이 타일이 보는 라이브 세션 (state.live 와 같은 모양)
    doc: null,          // 이 타일이 보는 것의 문서 (state.doc 과 같은 모양)
    title: "",
  };
  tile.overlay = MimiOverlay.attach({ overlay: el.querySelector(".overlay"), box: () => el });
  // 끌어서 놓을 때마다 저장합니다. 자막 자리는 한 벌이고 초점 타일에 적용됩니다.
  tile.overlay.onPos = (p) => { state.cuePos = p; persist(); };
  if (state.cuePos) tile.overlay.setPos(state.cuePos);
  el.querySelector(".tile-cover").addEventListener("click", () => setFocus(tile));
  el.querySelector(".tile-close").addEventListener("click", (e) => {
    e.stopPropagation();
    removeTile(tile);
  });
  // 덮개 위의 움직임도 전체화면 조절기를 부릅니다(iframe 위는 우리에게 오지 않습니다).
  el.addEventListener("mousemove", showFsControls);
  $("player-wrap").insertBefore(el, $("fs-controls"));
  state.tiles.push(tile);
  return tile;
}

/* 단일 소스 경로가 지나는 문. 타일을 하나로 접고 그것을 돌려줍니다 -- 녹화본을
 * 열 때, 라이브를 새로 시작할 때, 목록에서 다른 방송을 열 때. 다른 타일이 보던
 * 세션은 화면에서만 떼고 서버는 그대로 둡니다(detachLive 와 같은 뜻). */
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

/* 세션에서 손을 뗍니다. 플레이어는 그대로 둡니다 -- 「중단」이 자막만 멈추고
 * 방송은 계속 틀어 두는 것과 같은 규칙입니다. */
function detachTile(tile) {
  if (tile.live && tile.live.es) tile.live.es.close();
  tile.live = null;
  tile.doc = null;
  tile.overlay.clear();
  updateTileBar(tile);
}

/* 초점을 옮깁니다. 소리와 자막과 오른쪽 자막 내역이 함께 따라옵니다. */
function setFocus(tile, opts = {}) {
  const prev = focusedTile();
  if (!tile || prev === tile) return;
  if (prev) {
    if (prev.adapter) prev.adapter.setMuted(true);
    prev.overlay.clear();
    prev.el.classList.remove("focused");
  }
  tile.el.classList.add("focused");
  // 소리를 켜고 재생도 시킵니다. 초점을 옮긴 것은 사용자 조작(클릭·키)이라 재생이
  // 막히지 않고, 트위치는 소리를 켜는 순간 멈춰 서는 일이 있습니다.
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

/* 타일에 플레이어를 앉힙니다. 이미 있던 것은 치웁니다. */
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
    await tile.adapter.mount(tile.playerEl, src, {
      muted: !!opts.muted,
      onError: (msg, vid) => playerError(msg, vid, tile),
    });
  } catch (err) {
    playerError((err && err.message) || String(err), null, tile);
  }
}

/* 타일 띠: 사이트·제목·상태. 타일이 하나면 CSS 가 띠를 숨깁니다. */
const SITE_MARK = { youtube: "▶", twitch: "◉", hls: "≋", none: "" };

function updateTileBar(tile) {
  const live = tile.live;
  const title = (tile.doc && tile.doc.title) || tile.title || "";
  tile.el.querySelector(".tile-title").textContent = title;
  tile.el.querySelector(".tile-site").textContent = SITE_MARK[(tile.src || {}).site] || "";
  let st = "";
  if (live) {
    const m = live.lastStatus || {};
    const running = !live.state || LIVE_RUNNING.includes(live.state);
    st = running ? `${m.lines || live.store.size()}줄` : (LIVE_STATE[live.state] || live.state);
  }
  tile.el.querySelector(".tile-state").textContent = st;
  tile.el.classList.toggle("stopped", !!(live && live.state && !LIVE_RUNNING.includes(live.state)));
}

/* ---------- 배치 ----------
 *
 * 타일 수마다 기본 배치가 있고, 사용자가 고른 것은 prefs 에 남습니다. 1+2 와
 * 1+3 에서는 초점 타일이 큰 자리를 차지합니다 -- CSS 가 .focused 로 그렇게
 * 앉히므로 초점을 옮겨도 DOM 은 그대로입니다. */
const LAYOUTS = { "2": [2, 2], "1p2": [3, 3], "1p3": [4, 4], "2x2": [3, 4] };   // 이름 → [최소, 최대] 타일 수
const DEFAULT_LAYOUT = { 1: "1", 2: "2", 3: "1p2", 4: "2x2" };

function layoutFits(name, n) {
  const r = LAYOUTS[name];
  return !!r && n >= r[0] && n <= r[1];
}

function applyLayout(name) {
  const n = state.tiles.length;
  if (name && layoutFits(name, n)) {
    state.mvLayout = name;
  }
  const use = n <= 1 ? "1" : (layoutFits(state.mvLayout, n) ? state.mvLayout : DEFAULT_LAYOUT[n] || "2x2");
  const wrap = $("player-wrap");
  // className 을 통째로 갈지 않습니다 -- 전체화면의 fs-active 가 같은 요소에 붙습니다.
  [...wrap.classList].filter(c => c.startsWith("mv-")).forEach(c => wrap.classList.remove(c));
  wrap.classList.add("mv-" + use);
  document.querySelectorAll("[data-layout]").forEach(b => {
    b.classList.toggle("on", b.dataset.layout === use);
    b.disabled = !layoutFits(b.dataset.layout, n);
  });
  syncMvControls();
  requestAnimationFrame(applyCueSize);
}

/* 멀티뷰 조절기의 보임/숨김. 「＋ 타일」은 초점이 라이브일 때, 배치 단추는 타일이
 * 둘 이상일 때. */
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

/* 타일을 닫습니다 = 그 세션을 멈추고 묶음에서 뺍니다. 마지막 타일은 닫지 않습니다
 * -- 그것은 「중단」의 일입니다. */
async function removeTile(tile) {
  if (state.tiles.length <= 1) return;
  const wasFocus = tile === focusedTile();
  if (state.mv && tile.live) await mvRemove(state.mv.id, tile.live.id);
  detachTile(tile);
  if (tile.adapter) tile.adapter.destroy();
  tile.overlay.destroy();
  tile.el.remove();
  state.tiles.splice(state.tiles.indexOf(tile), 1);
  if (wasFocus) setFocus(state.tiles[0], { post: false });
  if (state.tiles.length === 1) state.mv = null;
  applyLayout();
}

/* ---------- 서버의 묶음 API ----------
 *
 * 모양은 여기 네 함수에만 있습니다. */
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

/* ---------- 멀티뷰 만들기·복원 ----------
 *
 * 「＋ 타일」은 지금 보는 방송 **옆에** 하나를 붙입니다. 첫 번째 붙이기가 묶음을
 * 만들고(보던 세션을 편입, 새 소스는 대기 세션으로), 그 뒤는 묶음에 더합니다.
 * 소리와 자막은 초점(지금 보던 것)에 그대로 남습니다. */
function liveStartArgs(lang) {
  return {
    lang, viewer_lang: $("viewer-lang").value, backend: state.backend,
    asr: state.asr, refine: state.refine, genre: currentGenre(),
    profile: document.querySelector('#add-form select[name="profile"]').value,
  };
}

async function addTile(url, lang, probe) {
  const cur = focusedTile();
  if (!cur || !cur.live) { jobError("먼저 라이브 방송을 여십시오. 타일은 그 옆에 붙습니다."); return; }
  if (state.tiles.length >= 4) { jobError("타일은 넷까지입니다."); return; }
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
    state.mv.members.push(res.id);
    members = [res];
  }
  for (const m of members) {
    if (tileBySession(m.id)) continue;
    const t = makeTile();
    // 새 세션은 yt-dlp 가 답하기 전이라 site·video_id 가 비어 있습니다. probe 가 방금
    // 알아낸 것으로 메워 플레이어를 바로 앉힙니다.
    await openSessionInTile(t, {
      ...m, url: m.url || url, site: m.site || probe.site,
      video_id: m.video_id || probe.id, channel: m.channel || probe.channel,
      title: m.title || probe.title || url,
    });
    await attachLive(t);
  }
  applyLayout();
}

/* 서버가 들고 있는 묶음을 화면에 그대로. 새로고침이나 목록에서 멤버를 눌렀을 때. */
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
  // 먹이기 전에 초점을 정해야 각 타일이 제 소리 상태(음소거)로 앉습니다.
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
