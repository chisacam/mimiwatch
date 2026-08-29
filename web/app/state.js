/* mimiwatch 화면 — 공유 상태와 작은 도우미. 다른 파일이 전부 여기 것을 씁니다 -- 그래서 맨 먼저 읽힙니다.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

/* mimiwatch player logic.
 *
 * The recorded-video flow needs no clock negotiation: every cue carries a
 * media-relative timestamp from transcription, and the YouTube iframe API
 * reports the same clock through getCurrentTime(). Lining them up is a
 * lookup, not an estimate -- the offset slider exists only for live streams
 * and for a viewer who wants to nudge it by taste.
 */
const $ = (id) => document.getElementById(id);

const state = {
  doc: null, cues: [], idx: -1, player: null, ready: false,
  mode: "both", offset: 0, showPrev: true, follow: true, panelHidden: false,
  libraryHidden: false, scriptOnly: false, capture: null, scriptWin: null,
  // 스크립트에서 줄을 누르면 무슨 일이 일어나는가. 기본은 예전 그대로
  // 「그 지점으로 이동」입니다. 켤 때마다 정합니다 -- 남겨 두면 다음에
  // 읽으러 왔다가 잘못 눌러 편집기가 열립니다.
  scriptMode: "read",
  // 번역 모드에서 고른 자막 번호. Set 입니다 -- 순서는 스크립트가 들고
  // 있으므로 여기서는 들었는지만 알면 됩니다.
  picked: new Set(), pickAnchor: null,
  genres: [],
  // 전체화면에 들어가기 직전의 플레이어 높이. 자막 크기 배율의 기준입니다.
  fsBaseHeight: 0, cuePx: 0,
  cuePos: null,        // { x, y } 비율. restore()가 채웁니다 -- DEFAULT_CUE_POS 참고
  backend: "local-gemma", asr: "tcpp-best", refine: true,
  backends: [], asrBackends: [], liveProfiles: [], jobId: null,
  models: null,        // /api/models 의 답. 모델·도구의 목록과 상태(engines.js)
  live: null,          // { id, es, store } while a broadcast is running (store: MimiCues)
  // 멀티뷰(tiles.js). tiles 는 화면의 칸들, focus 는 소리·자막·자막 내역이 따르는 칸,
  // mv 는 서버의 묶음 { id, focus, members }, mvLayout 은 고른 배치 이름.
  tiles: [], focus: null, mv: null, mvLayout: "",
};

/* A cue can hold one translation per backend, so switching backends is a
 * lookup when the work has already been done and a job when it has not. */
const trOf = (c) => c && c.translations ? c.translations[state.backend] : null;

/* Two different questions get asked about live, and conflating them is what
 * made "중단" leave the screen inconsistent with itself:
 *   state.live  -- a transcription session is running right now
 *   isLiveDoc() -- what the player is showing is a broadcast
 * Stopping subtitles answers only the first. The broadcast keeps playing, so
 * everything that reads "this is not a recording" -- cue lookup, script rows
 * keyed by id, the picker entry -- has to keep saying yes afterwards. */
const isLiveDoc = () => !!(state.doc && state.doc.live);

/* **받는 중**인 라이브인가. 끝난 방송과는 다르게 다뤄야 합니다.
 *
 * 유튜브는 방송이 끝나면 그것을 녹화본으로 남깁니다. 그때부터 이 세션의
 * 자막은 라이브 자막이 아니라 그 녹화본의 자막입니다 -- 시각으로 찾아야
 * 하고, 시각은 맞습니다(미디어 기준으로 적어 두었으니까요).
 *
 * 상태를 아직 못 받았으면 받는 중으로 봅니다. 시작 직후 한순간 그런데,
 * 그때 시각으로 찾으면 아직 도착하지 않은 자막을 찾는 셈이 됩니다. */
const isLiveReceiving = () => !!state.live
  && (!state.live.state || LIVE_RUNNING.includes(state.live.state));

/* A speaker chip is only information when it distinguishes someone. CAM++
 * gives one embedding per segment, so a four-way collab mixed into a single
 * stream comes back as S1 for every line -- a label that decorates without
 * telling the reader anything. Hold the chips until a second speaker
 * actually appears, and show them from then on. */
function showSpeakers() {
  const set = state.live ? state.live.speakers
            : new Set(state.cues.map(c => c.speaker).filter(Boolean));
  return !!set && set.size >= 2;
}

const chipFor = (c) => (showSpeakers() && c && c.speaker) ? c.speaker : "";

const PREFS = "mimiwatch.prefs";

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS)) || {}; }
  catch { return {}; }        // private windows and blocked storage both land here
}

function savePrefs(p) {
  try { localStorage.setItem(PREFS, JSON.stringify(p)); } catch { /* non-fatal */ }
}

/* ---------- 화면 위 자막 ----------
 *
 * 그리는 일은 web/overlay.js 가 합니다. 확장이 유튜브 페이지에 얹는 자막과
 * 같은 한 벌입니다 -- 모양·자리·끌기를 두 군데서 고치게 두지 않으려고
 * 뽑아 두었습니다. 여기서는 그 모듈에 값을 넣고 시계를 대 줍니다. */
/* 초점 타일의 오버레이입니다(tiles.js 의 initTiles/setFocus 가 넣습니다). 타일마다
 * 오버레이가 하나씩 있지만 그리는 것은 초점 타일의 것뿐입니다. */
let overlay = null;

/* 지금 무엇을 그려야 하는지 모듈에 알려 줍니다. 자막 목록·백엔드·라이브
 * 여부가 바뀔 때마다 부릅니다. */
function syncOverlayData() {
  if (!overlay) return;
  overlay.setData({
    cues: state.cues, backend: state.backend,
    live: isLiveDoc(), receiving: isLiveReceiving(),
    speakers: showSpeakers(),
  });
}

function cueAt(t) {
  syncOverlayData();
  return overlay ? overlay.cueAt(t) : -1;
}

function renderCue() {
  if (!overlay || !state.player || !state.player.ready) return;
  syncOverlayData();
  overlay.setView({ mode: state.mode, showPrev: state.showPrev });
  const i = overlay.render(state.player.getCurrentTime() + state.offset);
  if (i !== state.idx) {
    state.idx = i;
    markScript(i);
  }
}

const cueStart = (c) => (c.start != null ? c.start : c.t) || 0;

/* 자막 내역의 시각. 확장이 유튜브 페이지에 세우는 자막 내역(ext/panel.js)과 같은
 * 모양입니다 -- 한 시간을 넘으면 `1:02:03`, 아니면 `02:03`. 예전에는 여기가
 * 분:초만 적어서 두 시간짜리 방송의 `95:12`가 유튜브 진행 막대의 `1:35:12`와
 * 맞대어지지 않았습니다. 두 화면이 같은 발화를 같은 글자로 가리켜야 합니다. */
const fmt = (s) => {
  const t = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), x = t % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(x)}` : `${two(m)}:${two(x)}`;
};

/* 설정을 서버에서 받아 맞추기 전에는 저장하지 않습니다. 부팅 순서가
 * restore() → loadBackends()라서, 그 사이에 한 번이라도 저장하면 state에
 * 박아 둔 초기값이 사용자가 고른 값을 덮어씁니다. 그 뒤 loadBackends는
 * 방금 덮어쓴 값을 읽으므로 서버 기본값도 영영 이기지 못합니다. */
let booted = false;

function persist() {
  if (!booted) return;
  // applyModeForDoc()가 읽는 것과 같은 기준으로 적어야 합니다. 예전에는 여기서
  // `doc.translated`를 봤는데 라이브는 그 값이 첫 번역이 올 때까지 false라,
  // 라이브에서 고른 자막 모드가 `modeSame` 칸에 저장되고 다음에는
  // `modeTranslated` 칸에서 읽혀 기억되지 않았습니다.
  const translated = docHasTranslation();
  const prev = loadPrefs();
  savePrefs({
    ...prev,
    [translated ? "modeTranslated" : "modeSame"]: state.mode,
    size: +$("size").value, dim: +$("dim").value,
    cuePos: state.cuePos, showPrev: $("show-prev").checked,
    offset: +$("offset").value, viewerLang: $("viewer-lang").value,
    panelHidden: state.panelHidden, libraryHidden: state.libraryHidden,
    backend: state.backend,
    profile: (document.querySelector('#add-form select[name="profile"]') || {}).value,
    genre: (document.querySelector('#add-form select[name="genre"]') || {}).value,
    asr: state.asr, refine: state.refine,
    mvLayout: state.mvLayout,
  });
}

function restore() {
  const p = loadPrefs();
  if (p.viewerLang) $("viewer-lang").value = p.viewerLang;
  if (p.size) $("size").value = p.size;
  if (p.dim != null) $("dim").value = p.dim;
  // 예전에는 세로 자리만 「위치」 슬라이더의 퍼센트(p.pos)로 적었습니다.
  // 그 값을 가운데-그 높이로 옮겨 줍니다. 슬라이더 시절에 정해 둔 자리가
  // 갱신 한 번으로 아래 가운데로 튀지 않게요.
  const D = MimiOverlay.DEFAULT_POS;
  state.cuePos = p.cuePos ? { ...D, ...p.cuePos }
               : p.pos != null ? { x: D.x, y: p.pos / 100 }
               : { ...D };
  overlay.setPos(state.cuePos);
  state.mvLayout = p.mvLayout || "";
  if (p.showPrev != null) $("show-prev").checked = p.showPrev;
  if (p.offset != null) { $("offset").value = p.offset; state.offset = p.offset; }
  state.showPrev = $("show-prev").checked;
  applySize(+$("size").value);
  document.documentElement.style.setProperty("--cue-bg", (+$("dim").value / 100).toFixed(2));
  applyCuePos();
  $("offset-val").textContent = state.offset.toFixed(1) + "s";
  syncControlInputs();
  setScriptMode("read");     // 늘 읽기로 시작합니다. 저장하지 않습니다.
  setScriptView(p.scriptView || "both");
  if (p.scriptSize) {
    $("script-size").value = p.scriptSize;
    $("script").style.setProperty("--script-size", p.scriptSize + "px");
  }
  setPanel(!!p.panelHidden);
  setLibrary(!!p.libraryHidden);
}

const esc = (t) => String(t).replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- live ---------- */
/* 서버가 라이브 세션과 그 자막을 SQLite에 남기므로, 세션은 탭보다 오래 삽니다.
 * 상태 이름이 화면에 그대로 나오던 자리에 사람이 읽을 말을 붙입니다. */
const LIVE_RUNNING = ["starting", "loading", "running"];

const LIVE_STATE = {
  starting: "시작하는 중", loading: "모델 여는 중", running: "수신 중",
  stopped: "종료됨", interrupted: "중단됨 (서버 재시작)", error: "오류",
};
