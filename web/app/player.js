/* mimiwatch 화면 — 유튜브 플레이어, 화면 위 자막의 모드·크기·자리, 전체화면.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

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
  // 녹화본은 타일 하나로 봅니다. 다른 타일이 보던 방송은 화면에서만 뗍니다.
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
  // 로컬 파일 전사는 유튜브에 없는 영상입니다. 서버가 원본을 내주는 끝점을 틉니다.
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

/* IFrame API 가 다 읽히기를 기다립니다. 플레이어 자체는 어댑터(adapters.js 의
 * ytAdapter)가 타일마다 만듭니다. */
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

/* 유튜브가 내주는 코드를 사람이 읽을 말로 옮깁니다.
 *
 * 「임베드가 차단된 영상일 수 있습니다」 하나로 뭉뚱그리면 손댈 곳을 알 수
 * 없습니다. 특히 멤버십 전용 방송은 대개 임베드가 막혀 있어 101/150 으로
 * 오는데, 그것은 우리가 고칠 수 있는 문제가 아니라 유튜브에서 봐야 하는
 * 경우입니다. 자막은 서버가 따로 받아 적으므로 그때도 오른쪽 스크립트는
 * 그대로 읽힙니다. */
function embedErrorText(code) {
  if (code === 101 || code === 150) return t("player.embed.blocked");
  if (code === 100) return t("player.embed.notFound");
  if (code === 5) return t("player.embed.playbackFailed");
  if (code === 2) return t("player.embed.badId");
  return t("player.embed.unknown", { code });
}

/* 안내 상자는 타일을 통째로 덮습니다. 치우지 않으면 다음에 고른
 * 영상이 그 뒤에서 재생되어, 소리는 나는데 화면은 안내문인 상태가 됩니다.
 * 타일을 주지 않으면 초점 타일입니다. */
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
/* 자막 모드 단추는 두 벌입니다 -- 플레이어 아래의 것과 전체화면 상자 안의
 * 것. 어느 쪽을 눌러도 같은 자리를 거치게 하고, 표시도 함께 맞춥니다. */
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

/* 창 쪽 값을 전체화면 쪽 입력에 되비칩니다. 전체화면에 들어갈 때도 한 번
 * 부릅니다 -- 그때까지 손대지 않았으면 그 입력들은 빈 채로 있습니다. */
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

/* 자막 크기는 슬라이더 값 그대로가 아니라 **플레이어가 커진 만큼** 키웁니다.
 *
 * 창에서 고른 30px을 전체화면에서 그대로 쓰면 화면이 서너 배 커진 만큼
 * 자막만 작아 보입니다. 전체화면에 들어갈 때의 상자 높이를 기준으로 잡아
 * 두고 그 비율만큼 곱합니다. 창 모드에서는 기준이 없으므로 배율이 1이고,
 * 지금까지와 똑같이 동작합니다. */
function applyCueSize() {
  const t = focusedTile();
  if (!overlay || !t) return;
  const px = state.cuePx || +$("size").value;
  // 배율의 기준은 전체화면에 들어가기 직전의 **초점 타일** 높이입니다. 무엇을
  // 기준으로 삼을지는 부르는 쪽이 정합니다 -- 확장은 유튜브의 전체화면을 쓰므로
  // 기준이 다릅니다. 창 모드에서는 상자 전체를 기준으로 삼아, 타일 하나면 배율이
  // 1 이고(예전과 같음) 넷으로 나뉘면 그 칸에 맞게 줄어듭니다.
  const base = state.fsBaseHeight || $("player-wrap").clientHeight;
  const h = t.el.clientHeight;
  overlay.setSize(px, base && h ? h / base : 1);
}

/* ---------- 자막 자리 ----------
 *
 * 비율로 적어 두는 이유, 자르는 규칙, 끌기에 포인터 캡처가 필요한 이유는
 * 전부 web/overlay.js 에 있습니다. 여기서는 저장된 값을 모듈에 넣고
 * 되돌리기 단추를 이어 줄 뿐입니다. */
function applyCuePos() {
  if (overlay) overlay.reflow();
}

function resetCuePos() {
  if (overlay) overlay.resetPos();   // onPos 가 state.cuePos 와 저장을 맡습니다
}

const fsElement = () => document.fullscreenElement || document.webkitFullscreenElement;

function toggleFullscreen() {
  const wrap = $("player-wrap");
  if (fsElement()) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  } else {
    // 전체화면에 들어가기 직전의 초점 타일 높이를 기억해 둡니다. 들어간 뒤에
    // 재면 이미 커진 값이라 배율이 1이 됩니다.
    const t = focusedTile();
    state.fsBaseHeight = (t ? t.el : wrap).clientHeight;
    const req = wrap.requestFullscreen || wrap.webkitRequestFullscreen;
    // MW_I18N.t, not the short `t` -- in this scope `t` is the focused tile.
    if (!req) { fsFailed(MW_I18N.t("player.fullscreen.unsupported")); return; }
    Promise.resolve(req.call(wrap)).catch(err => fsFailed(err.message));
  }
}

/* 실패를 단추 자리에서 알립니다. jobError는 "재번역 실패"라고 적는
 * 번역 작업용이라 여기에 맞지 않습니다. */
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
  // 전체화면에 들어간 뒤 처음 열릴 때, 그동안 창에서 바꾼 값을 반영합니다.
  syncControlInputs();
  clearTimeout(fsIdleTimer);
  fsIdleTimer = setTimeout(hideFsControls, 2500);
}

function hideFsControls() {
  $("player-wrap").classList.remove("fs-active");
}

/* 커서는 감추지 않습니다.
 *
 * 커서가 iframe 위에 있는 동안 그 모양은 유튜브 문서가 정하므로, 바깥에서
 * cursor를 걸 방법이 없습니다. 투명한 막을 덮어 커서를 가져오는 방법을
 * 써 봤지만 듣지 않았습니다 -- 커서 모양은 **포인터가 움직일 때** 다시
 * 평가되는데, 멈춘 뒤에 막을 덮으면 다음 움직임까지 반영되지 않고 그
 * 움직임이 곧 막을 걷습니다. 게다가 막이 덮인 동안의 클릭은 유튜브에
 * 닿지 않아, 듣지도 않으면서 대가만 치르는 셈이었습니다. */
function onFullscreenChange() {
  const el = fsElement();
  const on = !!el;
  // iframe이 전체화면이 되면 자막은 그 하위 트리 밖이라 사라집니다. 여기까지
  // 왔다면 위의 방어가 뚫린 것이므로, 조용히 두지 않고 되돌립니다.
  if (el && (el.tagName === "IFRAME" || el.tagName === "VIDEO")) {
    console.warn("[fullscreen] 플레이어 요소가 전체화면이 되었습니다. 자막이 보이지 않습니다.");
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
  // 상자 크기가 바뀐 뒤에 재야 합니다. 전환 직후에는 아직 옛 크기입니다.
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
