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
  libraryHidden: false,
  genres: [],
  // 전체화면에 들어가기 직전의 플레이어 높이. 자막 크기 배율의 기준입니다.
  fsBaseHeight: 0, cuePx: 0,
  backend: "local-gemma", asr: "tcpp-best", refine: true,
  backends: [], asrBackends: [], liveProfiles: [], jobId: null,
  live: null,          // { id, es, byId } while a broadcast is running
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

/* ---------- cue lookup ---------- */
// Cues are sorted and non-overlapping, so a walking index beats a binary
// search here: playback advances by ~0.1s per tick and almost always lands
// on the same cue or the next one.
function cueAt(t) {
  const c = state.cues;
  if (!c.length) return -1;

  // Live subtitles cannot be looked up the way recorded ones are. A line is
  // published a few seconds after the words were spoken, so by the time it
  // exists the player has already moved past its timestamp -- a strict
  // window would show nothing, always. Hold the newest line that has started
  // until the next one takes over, which is how live captioning reads
  // anyway. The offset slider still shifts the whole track.
  if (isLiveDoc()) {
    // 자막은 도착하는 대로 띄웁니다. 오른쪽 스크립트에 줄이 뜨는 순간과
    // 같은 시점입니다.
    //
    // 지연 시청과 함께 자막도 붙잡아 두게 했다가 되돌렸습니다. 영상을 뒤로
    // 물리지 못하면 그 지연은 순전히 손해입니다 -- 영상은 최전선 그대로인데
    // 자막만 늦어집니다. 실측에서 seekTo가 라이브 임베드에 먹지 않았습니다
    // (되감기 전 11525, 3초 뒤 11528 -- 움직이지 않음).
    // 지연이 꺼져 있으면 자막은 언제나 영상보다 늦게 도착합니다. 시각으로
    // 맞추면 아무것도 보이지 않으므로, 가장 최근에 알아들은 것을 다음 줄이
    // 올 때까지 붙잡아 둡니다.
    const i = c.length - 1;
    const age = (Date.now() - (c[i].arrived || 0)) / 1000;
    return age > 20 ? -1 : i;
  }

  let i = state.idx >= 0 ? state.idx : 0;
  while (i > 0 && c[i].start > t) i--;
  while (i < c.length - 1 && c[i + 1].start <= t) i++;
  if (t < c[i].start) return -1;
  // Hold the line a moment past its end: a subtitle that blinks out during
  // the natural pause after a sentence reads as a dropped caption.
  if (t > c[i].end + 1.2) return -1;
  return i;
}

function renderCue() {
  if (!state.player || !state.ready) return;
  const t = state.player.getCurrentTime() + state.offset;
  const i = cueAt(t);
  const cur = i >= 0 ? state.cues[i] : null;
  const prev = i > 0 ? state.cues[i - 1] : null;

  // A cue that carries no translation (a fragment the model would only have
  // damaged) belongs to the source view. Showing it inside "번역만" or "둘 다"
  // reads as a line that failed to translate rather than one that never
  // needed to.
  const line = (c) => {
    if (!c) return "";
    if (state.mode === "source") return c.text;
    if (state.mode === "off") return "";
    return trOf(c) || "";
  };

  wrap($("cue-main"), line(cur), chipFor(cur));
  wrap($("cue-src"), state.mode === "both" && trOf(cur) ? cur.text : "");
  wrap($("cue-prev"), state.showPrev ? line(prev) : "", chipFor(prev));

  if (i !== state.idx) {
    state.idx = i;
    markScript(i);
  }
}
function wrap(el, text, speaker) {
  el.textContent = "";
  if (!text) return;
  const s = document.createElement("span");
  if (speaker) {
    const chip = document.createElement("b");
    chip.className = "spk-chip";
    chip.textContent = speaker;
    s.appendChild(chip);
  }
  s.appendChild(document.createTextNode(text));
  el.appendChild(s);
}

/* ---------- script panel ---------- */
function buildScript() {
  const box = $("script");
  box.textContent = "";
  state.cues.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "line";
    row.dataset.i = i;
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = fmt(c.start);
    const body = document.createElement("div");
    const tx = document.createElement("div");
    tx.className = "tx";
    tx.textContent = c.text;
    body.appendChild(tx);
    const trText = trOf(c);
    if (trText) {
      const tr = document.createElement("div");
      tr.className = "tr";
      tr.textContent = trText;
      body.appendChild(tr);
    }
    row.append(t, body);
    row.addEventListener("click", () => {
      if (state.player) { state.player.seekTo(c.start, true); state.player.playVideo(); }
    });
    box.appendChild(row);
  });
}
function refreshScriptRow(row, c) {
  const body = row.lastElementChild;
  body.textContent = "";
  const tx = document.createElement("div");
  tx.className = "tx";
  const chip1 = chipFor(c);
  if (chip1) {
    const chip = document.createElement("b");
    chip.className = "spk-chip";
    chip.textContent = chip1;
    tx.appendChild(chip);
  }
  tx.appendChild(document.createTextNode(c.text));
  body.appendChild(tx);
  const trText = trOf(c);
  if (trText) {
    const tr = document.createElement("div");
    tr.className = "tr";
    tr.textContent = trText;
    body.appendChild(tr);
  }
  row.classList.toggle("pending", c.kind === "final" && !trText);
}

function appendScriptLine(c) {
  const box = $("script");
  const existing = box.querySelector(`.line[data-id="${c.id}"]`);
  if (existing) {
    // 정제본이 같은 id의 줄을 갈아 끼웁니다. 글자 수가 달라지므로 높이도
    // 달라집니다.
    refreshScriptRow(existing, c);
    pinScriptToBottom();
    return;
  }
  const row = document.createElement("div");
  row.className = "line";
  row.dataset.id = c.id;
  const t = document.createElement("div");
  t.className = "t";
  t.textContent = fmt(c.start);
  const body = document.createElement("div");
  row.append(t, body);
  refreshScriptRow(row, c);
  row.addEventListener("click", () => {
    if (state.player) { state.player.seekTo(c.start, true); state.player.playVideo(); }
  });
  box.appendChild(row);
  pinScriptToBottom();
}

/* 라이브에서 「따라가기」는 특정 줄이 아니라 **바닥**을 좇는 것입니다.
 *
 * 예전에는 새 줄에 scrollIntoView({block:"end"})를 걸었습니다. 그런데 줄은
 * 붙은 뒤에도 높이가 계속 바뀝니다 -- 0.2초쯤 뒤에 번역이 도착해 한 줄이
 * 늘고(refreshScriptRow가 `.tr`을 붙입니다), 정제본이 오면 여러 줄이 하나로
 * 합쳐집니다. 그 자리들에서는 다시 맞추지 않았으므로, 맨 아래 줄이 조금씩
 * 화면 밖으로 밀려 잘려 보였습니다.
 *
 * 컨테이너를 바닥에 붙이면 높이가 어떻게 바뀌든 상관이 없습니다. 부드러운
 * 스크롤은 쓰지 않습니다 -- 자막이 몇 백 밀리초마다 들어오므로 애니메이션이
 * 끝나기 전에 다음 것이 시작되어 영영 바닥에 닿지 못합니다. */
function pinScriptToBottom() {
  if (!state.follow || !isLiveDoc()) return;
  const box = $("script");
  box.scrollTop = box.scrollHeight;
}

function markScript(i) {
  if (isLiveDoc()) return;  // live rows are keyed by cue id, not position
  const box = $("script");
  box.querySelectorAll(".line.on").forEach(el => el.classList.remove("on"));
  if (i < 0) return;
  const el = box.querySelector(`.line[data-i="${i}"]`);
  if (!el) return;
  el.classList.add("on");
  if (state.follow) el.scrollIntoView({ block: "center", behavior: "smooth" });
}
const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/* ---------- language status (R3.8/R3.9) ---------- */
function updateLangStatus() {
  const el = $("lang-status");
  if (!state.doc) { el.textContent = ""; return; }
  const src = state.doc.source_lang, viewer = $("viewer-lang").value;
  if (src === viewer) {
    el.className = "status";
    el.innerHTML = `원본 <b>${src}</b> · 내 언어와 같아 <b>번역 없음</b>`;
  } else if (state.doc.translated && state.doc.viewer_lang === viewer) {
    el.className = "status";
    el.innerHTML = `원본 <b>${src}</b> → <b>${viewer}</b> 번역됨`;
  } else {
    el.className = "status warn";
    el.innerHTML = `원본 <b>${src}</b> · <b>${viewer}</b> 번역본이 없습니다 (재전사 필요)`;
  }
}

/* ---------- loading ---------- */
async function loadVideo(id) {
  const doc = await (await fetch(`/api/video/${id}`)).json();
  state.doc = doc;
  state.cues = doc.cues || [];
  state.idx = -1;
  buildScript();
  updateLangStatus();
  renderBackendPicker();
  syncGenreToDoc();
  setNowTitle(doc.title);
  applyModeForDoc();
  if (state.player && state.ready) state.player.loadVideoById(id);
  else await createPlayer(id);
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

/* The IFrame API only builds a working player when the constructor is given a
 * videoId -- without one it leaves an empty container and never fires
 * onReady. So the player is created after the first video is known, not
 * before. */
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

async function createPlayer(videoId) {
  await whenApiReady();
  if (!(window.YT && window.YT.Player)) {
    playerError("YouTube IFrame API를 불러오지 못했습니다. 네트워크를 확인해 주세요.");
    return;
  }
  if (state.player) { state.player.loadVideoById(videoId); return; }
  state.player = new YT.Player("player", {
    videoId,
    // fs:0 은 유튜브의 전체화면 단추를 지웁니다. 그 단추는 **iframe**을
    // 전체화면 요소로 만드는데, 브라우저는 전체화면 요소의 하위 트리만
    // 그리므로 iframe 밖에 있는 자막 오버레이가 통째로 사라집니다.
    // iframe 안은 교차 출처라 그 단추를 가로챌 수 없으니, 지우고 우리
    // 단추를 대신 둡니다.
    playerVars: { rel: 0, modestbranding: 1, playsinline: 1, fs: 0 },
    events: {
      onReady: () => {
        state.ready = true;
        // fs:0 은 단추를 지울 뿐입니다. allowfullscreen 을 떼면 iframe은
        // 어떤 경로로도 전체화면 요소가 될 수 없습니다 -- 그래야 자막이
        // 사라지는 상태 자체가 만들어지지 않습니다.
        const f = document.querySelector("#player-wrap iframe");
        if (f) f.removeAttribute("allowfullscreen");
        setInterval(renderCue, 100);
      },
      onError: (e) => playerError(`영상을 재생할 수 없습니다 (code ${e.data}). ` +
                                  "임베드가 차단된 영상일 수 있습니다."),
    },
  });
}

function playerError(msg) {
  const wrap = document.getElementById("player-wrap");
  let box = document.getElementById("player-error");
  if (!box) {
    box = document.createElement("div");
    box.id = "player-error";
    box.className = "player-error";
    wrap.appendChild(box);
  }
  box.textContent = msg;
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
  pos:  v => { $("overlay").style.bottom = v + "%"; },
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
  for (const name of ["size", "dim", "pos"]) {
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
  const px = state.cuePx || +$("size").value;
  const base = state.fsBaseHeight;
  const h = $("player-wrap").clientHeight;
  const scale = base && h ? h / base : 1;
  const eff = Math.round(px * scale);
  $("overlay").style.fontSize = eff + "px";
  $("cue-main").style.fontSize = eff + "px";
}

const fsElement = () => document.fullscreenElement || document.webkitFullscreenElement;

function toggleFullscreen() {
  const wrap = $("player-wrap");
  if (fsElement()) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  } else {
    // 전체화면에 들어가기 직전의 높이를 기억해 둡니다. 들어간 뒤에 재면
    // 이미 커진 값이라 배율이 1이 됩니다.
    state.fsBaseHeight = wrap.clientHeight;
    const req = wrap.requestFullscreen || wrap.webkitRequestFullscreen;
    if (!req) { fsFailed("이 브라우저는 전체화면을 지원하지 않습니다."); return; }
    Promise.resolve(req.call(wrap)).catch(err => fsFailed(err.message));
  }
}

/* 실패를 단추 자리에서 알립니다. jobError는 "재번역 실패"라고 적는
 * 번역 작업용이라 여기에 맞지 않습니다. */
function fsFailed(msg) {
  state.fsBaseHeight = 0;
  const b = $("fullscreen");
  b.textContent = "⛶ 전체화면 불가";
  b.title = msg;
  console.error("[fullscreen]", msg);
  setTimeout(() => { b.textContent = "⛶ 전체화면"; }, 4000);
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
  if (el && el.tagName === "IFRAME") {
    console.warn("[fullscreen] iframe이 전체화면이 되었습니다. 자막이 보이지 않습니다.");
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    fsFailed("유튜브 플레이어가 자체 전체화면을 열었습니다. 아래 「전체화면」 단추를 쓰십시오.");
    return;
  }
  if (!on) state.fsBaseHeight = 0;
  $("fullscreen").classList.toggle("on", on);
  $("fullscreen").textContent = on ? "⛶ 창으로" : "⛶ 전체화면";
  if (on) {
    showFsControls();
  } else {
    clearTimeout(fsIdleTimer);
    $("player-wrap").classList.remove("fs-active");
  }
  // 상자 크기가 바뀐 뒤에 재야 합니다. 전환 직후에는 아직 옛 크기입니다.
  requestAnimationFrame(applyCueSize);
}
function bind() {
  // `.seg`가 아니라 `[data-mode]`로 좁힙니다. `.seg`는 🗑, ⚙, 스크립트 접기,
  // 전체화면처럼 자막과 무관한 단추도 달고 있는 공용 클래스입니다. 그것들을
  // 누르면 state.mode가 undefined가 되어 원문이 사라졌고(번역만 남습니다),
  // persist()가 그 값을 저장까지 했습니다.
  document.querySelectorAll("[data-mode]").forEach(b => {
    b.addEventListener("click", () => setMode(b.dataset.mode));
  });
  // 슬라이더도 두 벌입니다 -- 플레이어 아래의 것과 전체화면 상자 안의 것.
  // 창 쪽(#size/#dim/#pos)을 값의 주인으로 두고, 어느 쪽을 움직이든 그리로
  // 모은 뒤 양쪽 표시를 맞춥니다.
  for (const name of ["size", "dim", "pos"]) {
    $(name).addEventListener("input", e => setControl(name, e.target.value));
    document.querySelectorAll(`[data-ctl="${name}"]`).forEach(el =>
      el.addEventListener("input", e => setControl(name, e.target.value)));
  }
  $("show-prev").addEventListener("change", e => { state.showPrev = e.target.checked; persist(); renderCue(); });
  $("follow").addEventListener("change", e => {
    state.follow = e.target.checked;
    pinScriptToBottom();
  });
  $("offset").addEventListener("input", e => {
    state.offset = +e.target.value;
    $("offset-val").textContent = state.offset.toFixed(1) + "s";
    persist();
  });
  $("viewer-lang").addEventListener("change", () => { updateLangStatus(); persist(); });
  $("toggle-panel").addEventListener("click", () => setPanel(!state.panelHidden));
  $("toggle-library").addEventListener("click", () => setLibrary(!state.libraryHidden));
  $("backend-picker").addEventListener("change", e => selectBackend(e.target.value));
  $("open-manage").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleManage($("manage-menu").hidden);
  });
  // 바깥을 누르면 닫습니다. 메뉴 안을 누르는 것은 여닫기가 아닙니다.
  $("manage-menu").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => toggleManage(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleManage(false);
  });
  $("open-settings").addEventListener("click", () => { toggleManage(false); openSettings(); });
  $("settings-close").addEventListener("click", () => $("settings-dialog").close());
  $("shutdown").addEventListener("click", shutdownServer);
  $("form-back").addEventListener("click", showEngineList);
  $("engine-form").addEventListener("submit", saveEngine);
  document.querySelectorAll("[data-add]").forEach(b =>
    b.addEventListener("click", () => showEngineForm(b.dataset.add, null)));
  $("asr-picker").addEventListener("change", e => {
    setAsr(e.target.value);
    if (state.live) askLiveRestart();
  });
  document.querySelector('#add-form select[name="asr"]')
    .addEventListener("change", e => setAsr(e.target.value));
  document.querySelector('#add-form select[name="backend"]')
    .addEventListener("change", e => { state.backend = e.target.value;
                                       setBackendPickers(state.backend); persist(); });
  $("job-cancel").addEventListener("click", cancelJob);
  $("add-video").addEventListener("click", () => {
    // 열 때마다 지금 값으로 맞춥니다. 엔진을 지웠거나 「관리」에서 바꾼
    // 것이 대화상자에 반영되어 있어야 합니다.
    renderAsrPicker();
    fillEngineSelect(document.querySelector('#add-form select[name="backend"]'),
                     state.backends, state.backend, LOCKED.tr);
    $("add-dialog").showModal();
  });
  document.querySelector('#add-form input[name="refine"]')
    .addEventListener("change", e => { state.refine = e.target.checked; persist(); });
  $("add-form").addEventListener("submit", submitAdd);
  $("live-stop").addEventListener("click", stopLive);
  // Watching is a full-screen activity; reaching for the mouse to reclaim
  // width breaks it, so the toggle also answers to a key.
  document.addEventListener("keydown", (e) => {
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    if (e.key === "s") setPanel(!state.panelHidden);
    if (e.key === "v") setLibrary(!state.libraryHidden);
    // 유튜브의 f 단축키는 iframe 안에서만 듣습니다. 영상을 클릭한 뒤에는
    // 초점이 그 안에 있어 이 처리기까지 오지 않으므로, 겹칠 걱정은
    // 없습니다 -- 대신 그때는 fs:0 이 막아 줍니다.
    if (e.key === "f") toggleFullscreen();
  });
  $("fullscreen").addEventListener("click", toggleFullscreen);
  $("fs-exit").addEventListener("click", toggleFullscreen);
  // 조절기는 마우스가 움직일 때만 뜨고 잠시 뒤 사라집니다. 영상 위에 계속
  // 떠 있으면 보는 것을 방해합니다.
  $("player-wrap").addEventListener("mousemove", showFsControls);
  $("fs-controls").addEventListener("mousemove", showFsControls);
  // 영상 위의 움직임은 iframe이 삼키므로 위의 처리기까지 오지 않습니다.
  // 이 띠만이 전체화면에서 조절기를 다시 부르는 길입니다.
  $("fs-hotzone").addEventListener("mouseenter", showFsControls);
  $("fs-hotzone").addEventListener("mousemove", showFsControls);
  // 영상 위 더블클릭은 받을 수 없습니다. iframe이 상자를 꽉 채우고 있어
  // letterbox 여백까지 iframe의 것이라, 그 두 번 누름은 유튜브가 가져갑니다.
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);
  // 전체화면에서 창 크기가 바뀌면(다른 화면으로 옮기는 등) 배율도 바뀝니다.
  window.addEventListener("resize", () => {
    if (fsElement()) applyCueSize();
    if (!$("manage-menu").hidden) toggleManage(true);
  });
}

function setPanel(hidden) {
  state.panelHidden = hidden;
  $("layout").classList.toggle("panel-hidden", hidden);
  $("toggle-panel").textContent = hidden ? "스크립트 ◂" : "스크립트 ▸";
  $("toggle-panel").classList.toggle("on", hidden);
  persist();
}

function toggleManage(open) {
  const menu = $("manage-menu"), btn = $("open-manage");
  menu.hidden = !open;
  btn.classList.toggle("on", open);
  btn.setAttribute("aria-expanded", String(!!open));
  if (!open) return;
  // fixed 라 자리를 직접 잡아 줍니다. 단추 아래, 오른쪽 끝을 맞춥니다.
  const r = btn.getBoundingClientRect();
  menu.style.top = `${Math.round(r.bottom + 6)}px`;
  menu.style.left = "auto";
  menu.style.right = `${Math.round(window.innerWidth - r.right)}px`;
}

function setLibrary(hidden) {
  state.libraryHidden = hidden;
  $("layout").classList.toggle("library-hidden", hidden);
  $("toggle-library").textContent = hidden ? "▸ 영상" : "◧ 영상";
  $("toggle-library").classList.toggle("on", hidden);
  persist();
}
/* 설정을 서버에서 받아 맞추기 전에는 저장하지 않습니다. 부팅 순서가
 * restore() → loadBackends()라서, 그 사이에 한 번이라도 저장하면 state에
 * 박아 둔 초기값이 사용자가 고른 값을 덮어씁니다. 그 뒤 loadBackends는
 * 방금 덮어쓴 값을 읽으므로 서버 기본값도 영영 이기지 못합니다. */
let booted = false;

function persist() {
  if (!booted) return;
  const translated = !!(state.doc && state.doc.translated);
  const prev = loadPrefs();
  savePrefs({
    ...prev,
    [translated ? "modeTranslated" : "modeSame"]: state.mode,
    size: +$("size").value, dim: +$("dim").value,
    pos: +$("pos").value, showPrev: $("show-prev").checked,
    offset: +$("offset").value, viewerLang: $("viewer-lang").value,
    panelHidden: state.panelHidden, libraryHidden: state.libraryHidden,
    backend: state.backend,
    profile: (document.querySelector('#add-form select[name="profile"]') || {}).value,
    genre: (document.querySelector('#add-form select[name="genre"]') || {}).value,
    asr: state.asr, refine: state.refine,
  });
}
function restore() {
  const p = loadPrefs();
  if (p.viewerLang) $("viewer-lang").value = p.viewerLang;
  if (p.size) $("size").value = p.size;
  if (p.dim != null) $("dim").value = p.dim;
  if (p.pos != null) $("pos").value = p.pos;
  if (p.showPrev != null) $("show-prev").checked = p.showPrev;
  if (p.offset != null) { $("offset").value = p.offset; state.offset = p.offset; }
  state.showPrev = $("show-prev").checked;
  applySize(+$("size").value);
  document.documentElement.style.setProperty("--cue-bg", (+$("dim").value / 100).toFixed(2));
  $("overlay").style.bottom = $("pos").value + "%";
  $("offset-val").textContent = state.offset.toFixed(1) + "s";
  syncControlInputs();
  setPanel(!!p.panelHidden);
  setLibrary(!!p.libraryHidden);
}

/* ---------- translation backends ---------- */
async function loadBackends() {
  const cfg = await (await fetch("/api/backends")).json();
  state.backends = cfg.backends;
  const p = loadPrefs();
  state.liveProfiles = cfg.live_profiles || [];
  renderProfilePicker();
  state.genres = cfg.genres || [];
  renderGenrePicker();
  state.asrBackends = cfg.asr_backends || [];
  const p0 = loadPrefs();
  const asrIds = state.asrBackends.map(b => b.id);
  state.asr = asrIds.includes(p0.asr) ? p0.asr : (cfg.asr_active || "tcpp-best");
  if (p0.refine != null) state.refine = !!p0.refine;
  document.querySelector('#add-form input[name="refine"]').checked = state.refine;
  renderAsrPicker();
  booted = true;      // 여기부터는 state가 설정과 맞춰졌으므로 저장해도 됩니다
  const ids = cfg.backends.map(b => b.id);
  // A remembered backend can disappear when the config is edited or renamed.
  // Falling back keeps a stale preference from asking the server for a
  // backend that no longer exists.
  state.backend = ids.includes(p.backend) ? p.backend
                : (ids.includes(cfg.active) ? cfg.active : cfg.backends[0].id);
  if (state.backend !== p.backend) persist();
  renderBackendPicker();
}
function renderBackendPicker() {
  const pick = $("backend-picker");
  pick.textContent = "";
  state.backends.forEach(b => {
    const o = document.createElement("option");
    o.value = b.id;
    // "(미번역)" means "this backend has not run over this file yet", which
    // is a statement about a finished transcript. A live session translates
    // as it goes, so the label would be wrong the moment the first line
    // lands.
    const done = state.doc && (state.doc.backends_done || []).includes(b.id);
    o.textContent = b.label + (state.doc && !isLiveDoc() && !done ? " (미번역)" : "");
    pick.appendChild(o);
  });
  pick.value = state.backend;
  // 대화상자 쪽도 같은 값을 가리켜야 합니다. "(미번역)" 표시는 열려 있는
  // 영상에 대한 말이라 대화상자에는 붙이지 않습니다 -- 거기서 고르는 것은
  // 아직 없는 영상의 엔진입니다.
  fillEngineSelect(document.querySelector('#add-form select[name="backend"]'),
                   state.backends, state.backend, LOCKED.tr);
  // Nothing to translate when the speaker already uses the viewer's language.
  const same = state.doc && state.doc.source_lang === state.doc.viewer_lang;
  $("backend-field").hidden = !!same;
}

async function selectBackend(id) {
  state.backend = id;
  persist();
  if (isLiveDoc()) {
    // Nothing to re-translate: lines already on screen keep what they got,
    // and everything from here uses the new backend. A broadcast whose
    // subtitles were stopped has no session left to tell, and it is no saved
    // video either, so the picker is all there is to update.
    state.doc.backends_done = [...new Set([...(state.doc.backends_done || []), id])];
    if (state.live) {
      await fetch("/api/live/backend", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: state.live.id, backend: id }),
      }).catch(() => {});
    }
    renderBackendPicker();
    return;
  }
  const done = (state.doc.backends_done || []).includes(id);
  if (!done && state.doc.source_lang !== state.doc.viewer_lang) {
    await runTranslateJob(state.doc.id, id);
  }
  buildScript(); applyModeForDoc(); renderBackendPicker();
}

async function runTranslateJob(video, backend) {
  const box = $("job");
  box.hidden = false; box.classList.remove("error");
  $("job-cancel").disabled = false;
  document.querySelector(".job-label").textContent = "재번역 중…";
  $("job-label") && ($("job-label").textContent = "재번역 중…");
  $("job-source").textContent = "";
  const res = await (await fetch("/api/translate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video, backend, genre: currentGenre() }),
  })).json();
  if (res.error) { jobError(res.error); return; }
  state.jobId = res.id;

  while (true) {
    await new Promise(r => setTimeout(r, 700));
    const st = await (await fetch(`/api/job/${res.id}`)).json();
    const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
    $("job-fill").style.width = pct + "%";
    $("job-count").textContent = `${st.done}/${st.total}  (건너뜀 ${st.skipped})`;
    // Say plainly which model produced the lines so far. "폴백 중"에 대한
    // 의심을 숫자로 답합니다.
    const srcEl = $("job-source");
    srcEl.classList.toggle("local", !!st.degraded);
    srcEl.textContent = st.degraded
      ? `원격 응답 없음 (실패 ${st.failures}회) · 로컬 대체 ${st.by_local}건`
      : `원격 번역 ${st.by_remote}건` + (st.by_local ? ` · 로컬 대체 ${st.by_local}건` : "");
    box.classList.toggle("error", !!st.degraded);
    // 서버가 재시작되면 작업 스레드는 사라지고 상태만 남습니다. 계속 폴링하면
    // 영원히 끝나지 않으므로 종료 상태로 취급합니다.
    if (st.state === "error" || st.state === "interrupted") { jobError(st.error); return; }
    if (st.state === "cancelled") {
      $("job-count").textContent = `중단됨 · ${st.done}/${st.total}까지 저장`;
      setTimeout(() => { box.hidden = true; }, 3000);
      const doc = await (await fetch(`/api/video/${video}`)).json();
      state.doc = doc; state.cues = doc.cues; state.idx = -1;
      buildScript(); applyModeForDoc(); renderBackendPicker();
      return;
    }
    if (st.state === "done") {
      $("job-count").textContent = `완료 ${st.done}건 · ${st.elapsed}초`
        + (st.degraded ? `  · 원격 실패 ${st.failures}회, 로컬로 대체됨` : "");
      setTimeout(() => { box.hidden = true; }, 2500);
      const doc = await (await fetch(`/api/video/${video}`)).json();
      state.doc = doc; state.cues = doc.cues; state.idx = -1;
      return;
    }
  }
}
const PHASE_LABEL = {
  probe: "영상 정보 확인", download: "오디오 내려받는 중",
  transcribe: "전사 중", translate: "번역 중", done: "완료",
};

async function submitAdd(e) {
  if (e.submitter && e.submitter.value === "cancel") return;
  const f = e.target;
  const url = f.url.value.trim();
  if (!url) return;
  // Which pipeline a URL belongs to is the server's call, not the user's.
  const probe = await (await fetch("/api/probe", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  })).json();
  if (probe.error) { jobError(probe.error); return; }
  f.url.value = "";

  if (probe.is_live) {
    await startLive(url, f.lang.value || null, probe);
    return;
  }
  const res = await (await fetch("/api/transcribe", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url, lang: f.lang.value || null, asr: state.asr,
      speakers: f.speakers.checked, genre: currentGenre(),
      viewer_lang: $("viewer-lang").value, backend: state.backend,
    }),
  })).json();
  if (res.error) { jobError(res.error); return; }
  await watchTranscribe(res.id);
}

async function watchTranscribe(jobId) {
  const box = $("job");
  state.jobId = jobId;
  box.hidden = false; box.classList.remove("error");
  $("job-cancel").disabled = false;
  $("job-source").textContent = "";

  while (true) {
    await new Promise(r => setTimeout(r, 800));
    const st = await (await fetch(`/api/job/${jobId}`)).json();
    document.querySelector(".job-label").textContent =
      (PHASE_LABEL[st.phase] || st.phase) + (st.title ? ` · ${st.title.slice(0, 28)}` : "");
    // The download reports no numbers, so an indeterminate bar is honest
    // where a 0% bar would look stuck.
    const pct = st.total ? Math.round(st.done / st.total * 100) : null;
    $("job-fill").style.width = pct === null ? "12%" : pct + "%";
    $("job-count").textContent = st.total
      ? `${st.done}/${st.total}` + (st.phase === "transcribe" ? "초" : "")
      : "…";
    if (st.asr_fallback) {
      $("job-source").classList.add("local");
      $("job-source").textContent = "외부 전사 엔진 실패 · hayamimi 로컬로 대체";
    } else if (st.phase === "translate" && (st.by_remote || st.by_local)) {
      $("job-source").classList.toggle("local", !!st.degraded);
      $("job-source").textContent = st.degraded
        ? `원격 응답 없음 · 로컬 대체 ${st.by_local}건`
        : `원격 번역 ${st.by_remote}건`;
    }
    if (st.state === "error" || st.state === "interrupted") { jobError(st.error); return; }
    if (st.state === "cancelled") {
      $("job-count").textContent = "중단됨";
      setTimeout(() => { box.hidden = true; }, 3000);
      await refreshVideoList(st.video);
      return;
    }
    if (st.state === "done") {
      $("job-count").textContent = `완료 · ${st.elapsed}초`;
      setTimeout(() => { box.hidden = true; }, 2500);
      await refreshVideoList(st.video);
      return;
    }
  }
}

/* 장르는 「콘텐츠 유형」과 다른 축입니다. 유형은 발화를 몇 초에 끊을지를
 * 정하고(라이브 전용), 장르는 그 발화를 어떤 어휘로 옮길지를 정합니다 --
 * 녹화본에도 필요합니다. */
function renderGenrePicker() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  if (!sel) return;
  sel.textContent = "";
  state.genres.forEach(g => {
    const o = document.createElement("option");
    o.value = g.id;
    o.textContent = g.label;
    sel.appendChild(o);
  });
  const saved = loadPrefs().genre;
  sel.value = state.genres.some(g => g.id === saved) ? saved : "general";
  sel.onchange = showGenreHint;
  showGenreHint();
}

function showGenreHint() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  const hint = $("genre-hint");
  const g = state.genres.find(x => x.id === (sel || {}).value);
  if (hint && g) hint.textContent = g.hint;
}

function currentGenre() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  return (sel && sel.value) || "general";
}

/* 열려 있는 영상의 장르를 선택기에 되비칩니다. 되비치지 않으면 다른 영상에
 * 마지막으로 고른 값이 남아, 다시 번역할 때 엉뚱한 프롬프트가 갑니다. */
function syncGenreToDoc() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  const g = (state.doc || {}).genre;
  if (sel && g && state.genres.some(x => x.id === g)) {
    sel.value = g;
    showGenreHint();
  }
}

function renderProfilePicker() {
  const sel = document.querySelector('#add-form select[name="profile"]');
  if (!sel) return;
  sel.textContent = "";
  state.liveProfiles.forEach(p => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = `${p.label} · ${p.max_speech}초마다 끊기`;
    sel.appendChild(o);
  });
  const saved = loadPrefs().profile;
  sel.value = state.liveProfiles.some(p => p.id === saved) ? saved : "broadcast";
}





/* ---------- engine manager ----------
 * Both engine kinds are managed in one place, outside the add-video flow.
 * Putting the transcription picker inside that dialog meant the only way to
 * delete an engine was to pretend to add a video. */
function openSettings() {
  showEngineList();
  $("settings-dialog").showModal();
}

/* 서버를 명시적으로 끕니다.
 *
 * 프로세스를 죽이는 것과 다릅니다. 서버가 받는 중인 방송을 먼저 닫아
 * 「종료됨」으로 기록한 뒤에 멈추므로, 사용자가 스스로 끈 것과 서버가 죽은
 * 것이 지난 방송 목록에서 구분됩니다. */
async function shutdownServer() {
  // 이 탭이 라이브를 보고 있지 않아도 서버는 받고 있을 수 있습니다 -- 다른
  // 탭에서 시작했거나, 이 탭을 열기 전부터 돌고 있었거나. 그래서 여기서는
  // 문구만 고르고, 실제로 몇 건을 닫았는지는 응답에서 받습니다.
  const msg = state.live
    ? "받는 중인 방송을 닫고 서버를 종료합니다. 여기까지 받아 적은 자막은 남습니다.\n\n계속할까요?"
    : "서버를 종료합니다. 받는 중인 방송이 있으면 함께 닫습니다.\n\n계속할까요?";
  if (!confirm(msg)) return;

  const btn = $("shutdown");
  const hint = $("shutdown-group").querySelector(".hint");
  btn.disabled = true;
  btn.textContent = "종료하는 중…";

  let stopped = null;                     // null = 답을 못 받음
  try {
    const r = await fetch("/api/shutdown", { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    stopped = (await r.json()).sessions_stopped || 0;
  } catch (e) {
    // 답이 없다고 종료된 것은 아닙니다. 서버가 정말 멈췄으면 다음 요청도
    // 실패하고, 살아 있으면 답합니다 -- 물어보고 나서 적습니다.
    //
    // 예전에는 여기서 곧바로 "종료됨"이라고 적었는데, 그러면 서버가 이
    // 경로를 모르는 판(구버전이 돌고 있어 404가 나는 경우)에도 껐다고
    // 말하게 됩니다. 껐다고 믿고 자리를 뜨면 방송은 계속 받아집니다.
    const alive = await fetch("/api/backends", { cache: "no-store" })
      .then(r => r.ok).catch(() => false);
    if (alive) {
      $("shutdown-group").classList.add("done");
      hint.textContent =
        `서버를 멈추지 못했습니다 (${e.message}). 서버가 이 기능을 모르는 ` +
        "예전 판일 수 있습니다. 터미널에서 Ctrl-C 로 끄십시오.";
      btn.disabled = false;
      btn.textContent = "종료";
      return;
    }
  }

  // 여기까지 왔으면 서버는 멈췄습니다. 새로고침해도 돌아올 곳이 없으므로
  // 화면을 그대로 두고 무엇이 끝났는지만 적습니다.
  $("shutdown-group").classList.add("done");
  hint.textContent =
    (stopped ? `방송 ${stopped}건을 닫고 서버를 종료했습니다. `
             : "서버를 종료했습니다. ") +
    "이 탭은 더 이상 갱신되지 않습니다. 다시 켜려면 터미널에서 ./run.sh.";
  btn.textContent = "종료됨";
  stopLive();
}

function showEngineList() {
  $("engine-form").hidden = true;
  $("settings-body").hidden = false;
  renderEngineList("asr");
  renderEngineList("tr");
}

// 지울 수 없는 기본 엔진. 목록에서 사라지면 고를 것이 없어집니다.
const LOCKED = { asr: "tcpp-best", tr: "local-m2m100" };

function enginesOf(kind) {
  return kind === "asr" ? state.asrBackends : state.backends;
}

function renderEngineList(kind) {
  const box = $(kind === "asr" ? "asr-list" : "tr-list");
  box.textContent = "";
  enginesOf(kind).forEach(b => {
    const row = document.createElement("div");
    row.className = "engine-row" + (b.id === LOCKED[kind] ? " locked" : "");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = b.label || b.id;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = b.backend === "openai"
      ? `${b.base_url} · ${b.model}` : "로컬 실행 · 외부 전송 없음";
    name.appendChild(meta);

    const edit = document.createElement("button");
    edit.textContent = "수정";
    edit.disabled = b.backend !== "openai";
    edit.addEventListener("click", () => showEngineForm(kind, b));

    const del = document.createElement("button");
    del.className = "danger";
    del.textContent = "삭제";
    if (b.id === LOCKED[kind]) {
      del.title = "기본 로컬 엔진은 삭제할 수 없습니다";
      del.disabled = true;
    } else {
      del.addEventListener("click", () => removeEngine(kind, b));
    }
    row.append(name, edit, del);
    box.appendChild(row);
  });
}

function showEngineForm(kind, entry) {
  const f = $("engine-form");
  f.dataset.kind = kind;
  f.dataset.editing = entry ? entry.id : "";
  $("form-title").textContent =
    (entry ? "엔진 수정" : "엔진 추가") + (kind === "asr" ? " · 전사" : " · 번역");
  const seed = entry || { label: "", id: "", base_url: "http://localhost:1234",
                          model: kind === "asr" ? "whisper-1" : "",
                          api_key: "", window_s: 240, min_chars: 0 };
  ["label", "id", "base_url", "model", "api_key"].forEach(k => { f[k].value = seed[k] || ""; });
  f.window_s.value = seed.window_s || 240;
  f.min_chars.value = seed.min_chars || 0;
  $("field-window").hidden = kind !== "asr";
  $("field-minchars").hidden = kind !== "tr";
  f.id.readOnly = !!entry;          // the id keys stored translations
  $("settings-body").hidden = true;
  f.hidden = false;
}

async function saveEngine(e) {
  e.preventDefault();
  const f = e.target;
  const kind = f.dataset.kind;
  const entry = {
    id: f.id.value.trim(), label: f.label.value.trim(), backend: "openai",
    base_url: f.base_url.value.trim(), model: f.model.value.trim(),
    api_key: f.api_key.value.trim(),
  };
  if (kind === "asr") entry.window_s = Math.max(30, parseInt(f.window_s.value, 10) || 240);
  else entry.min_chars = Math.max(0, parseInt(f.min_chars.value, 10) || 0);
  if (!entry.id || !entry.base_url || !entry.model) return;

  const url = kind === "asr" ? "/api/asr-backends" : "/api/backends";
  const cfg = await (await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  })).json();
  adoptConfig(cfg);
  showEngineList();
}

async function removeEngine(kind, b) {
  if (!confirm(`'${b.label || b.id}' 을(를) 삭제할까요?`)) return;
  const url = kind === "asr" ? "/api/asr-backends/delete" : "/api/backends/delete";
  const cfg = await (await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: b.id }),
  })).json();
  if (cfg.error) { alert(cfg.error); return; }
  adoptConfig(cfg);
  showEngineList();
}

function adoptConfig(cfg) {
  if (cfg.backends) state.backends = cfg.backends;
  if (cfg.asr_backends) state.asrBackends = cfg.asr_backends;
  // A picker still pointing at something that was just deleted would ask the
  // server for an engine that no longer exists.
  if (!state.backends.some(b => b.id === state.backend)) state.backend = LOCKED.tr;
  if (!state.asrBackends.some(b => b.id === state.asr)) state.asr = LOCKED.asr;
  persist();
  renderAsrPicker();
  renderBackendPicker();
}

/* 엔진 선택기는 두 자리에 있습니다 -- 「관리」 메뉴와 「영상 추가」 대화상자.
 *
 * 넣는 순간 그 엔진으로 전사가 시작되므로, 넣기 전에 고를 수 있어야 합니다.
 * 예전에는 헤더에서만 고를 수 있어서, 대화상자를 열어 둔 채로는 바꿀 수
 * 없었습니다. 두 자리가 같은 값을 가리키므로 어느 쪽에서 고르든 같습니다. */
function fillEngineSelect(sel, entries, current, fallback) {
  if (!sel) return current;
  sel.textContent = "";
  entries.forEach(b => {
    const o = document.createElement("option");
    o.value = b.id; o.textContent = b.label || b.id;
    sel.appendChild(o);
  });
  sel.value = entries.some(b => b.id === current) ? current : fallback;
  return sel.value;
}

function renderAsrPicker() {
  state.asr = fillEngineSelect($("asr-picker"), state.asrBackends,
                               state.asr, LOCKED.asr);
  fillEngineSelect(document.querySelector('#add-form select[name="asr"]'),
                   state.asrBackends, state.asr, LOCKED.asr);
}

function setAsr(id) {
  state.asr = id;
  const a = $("asr-picker");
  const b = document.querySelector('#add-form select[name="asr"]');
  if (a) a.value = id;
  if (b) b.value = id;
  persist();
}

function setBackendPickers(id) {
  const a = $("backend-picker");
  const b = document.querySelector('#add-form select[name="backend"]');
  if (a && [...a.options].some(o => o.value === id)) a.value = id;
  if (b) b.value = id;
}

const esc = (t) => String(t).replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function hideLiveNotice() {
  const box = $("live-notice");
  box.hidden = true;
  box.textContent = "";
}

function showLiveNotice(text) {
  const box = $("live-notice");
  box.textContent = text;
  box.hidden = false;
}

/* 돌아가는 세션의 전사 엔진을 그 자리에서 갈아 끼웁니다.
 *
 * 예전에는 「새 엔진으로 다시 시작」을 눌러야 했습니다. 그러면 세션 id가
 * 바뀌고 자막은 세션 id로 저장되므로, **그때까지의 스크립트가 통째로
 * 사라졌습니다.** 번역기는 이미 세션 안에서 갈아 끼우고 있었으니 전사기만
 * 그럴 이유가 없습니다. */
async function askLiveRestart() {
  const stale = state.live && state.live.state
                && !LIVE_RUNNING.includes(state.live.state);
  if (!state.live || state.live.asr === state.asr || stale) {
    hideLiveNotice();
    return;
  }
  const res = await (await fetch("/api/live/asr", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: state.live.id, asr: state.asr }),
  })).json();
  if (res.error) {
    // 실패하면 서버는 쓰던 엔진을 그대로 씁니다. 화면의 선택기도
    // 되돌려 놓아야 둘이 어긋나지 않습니다.
    setAsr(state.live.asr);
    showLiveNotice(`전사 엔진을 바꾸지 못했습니다 — ${res.error}`);
    return;
  }
  state.live.asr = state.asr;
  hideLiveNotice();
}

/* ---------- live ---------- */
/* 서버가 라이브 세션과 그 자막을 SQLite에 남기므로, 세션은 탭보다 오래 삽니다.
 * 상태 이름이 화면에 그대로 나오던 자리에 사람이 읽을 말을 붙입니다. */
const LIVE_RUNNING = ["starting", "loading", "running"];
const LIVE_STATE = {
  starting: "시작하는 중", loading: "모델 여는 중", running: "수신 중",
  stopped: "종료됨", interrupted: "중단됨 (서버 재시작)", error: "오류",
};

async function startLive(url, lang, probe) {
  stopLive();
  const res = await (await fetch("/api/live/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url, lang, viewer_lang: $("viewer-lang").value, backend: state.backend,
      asr: state.asr, refine: state.refine, genre: currentGenre(),
      profile: document.querySelector('#add-form select[name="profile"]').value,
    }),
  })).json();
  if (res.error) { jobError(res.error); return; }

  // A live session has no cue file: cues arrive over SSE and accumulate in
  // place, and `t` is media seconds -- the same axis the YouTube player
  // reports for a live stream, so the usual lookup still applies.
  state.doc = { id: probe.id, title: probe.title, source_lang: lang || "",
                viewer_lang: $("viewer-lang").value, translated: false,
                backends_done: [state.backend], live: true };
  state.cues = [];
  state.idx = -1;
  state.live = { id: res.id, byId: new Map(), es: null, speakers: new Set(),
                 url, lang, probe, asr: state.asr };
  buildScript();
  renderBackendPicker();
  applyModeForDoc();
  addLiveToPicker(probe, res.id);
  $("job").hidden = true;      // a stale re-translation box is not this session's
  state.jobId = null;
  $("live-badge").hidden = false;
  $("offset-wrap").style.display = "flex";
  await attachLive(res.id, probe.id);
}

/* 이미 있는 세션을 다시 엽니다 -- 탭을 새로고침했거나, 서버가 재시작되어
 * 수신은 끊겼지만 받아 적은 자막은 남아 있는 경우입니다. 자막은 서버가 SSE
 * 접속 직후에 그대로 되돌려 주므로, 여기서는 라이브를 새로 시작할 때와 같은
 * 그릇만 만들어 두면 나머지는 같은 이벤트 경로를 탑니다. */
async function resumeLive(sessionId) {
  if (state.live && state.live.id === sessionId) return;   // 이미 보고 있음
  detachLive();
  const st = await (await fetch(`/api/live/status/${sessionId}`)).json();
  if (!st.id) { jobError(st.error || "세션을 찾을 수 없습니다"); return; }
  const running = LIVE_RUNNING.includes(st.state);

  // 그때 쓰던 번역 백엔드로 맞춥니다. 저장된 번역문은 그 백엔드가 만든 것이라,
  // 지금 고른 백엔드 칸에 넣으면 하지 않은 일을 했다고 표시하게 됩니다.
  if (st.backend && state.backends.some(b => b.id === st.backend)) {
    state.backend = st.backend;
  }
  // 전사 엔진도 마찬가지입니다. 세션이 실제로 쓰는 것과 선택기가 가리키는
  // 것이 다르면, 다음에 무엇을 바꿔도 화면과 서버가 어긋난 채로 갑니다.
  if (st.asr_backend && state.asrBackends.some(b => b.id === st.asr_backend)) {
    setAsr(st.asr_backend);
  }
  state.doc = { id: st.video_id || "", title: st.title || st.url,
                source_lang: st.source_lang || "", viewer_lang: st.viewer_lang,
                translated: false, backends_done: [st.backend], live: true };
  state.cues = [];
  state.idx = -1;
  state.live = { id: st.id, byId: new Map(), es: null, speakers: new Set(),
                 url: st.url, lang: st.source_lang || null, state: st.state,
                 probe: { id: st.video_id, title: st.title },
                 asr: st.asr_backend || "" };
  buildScript();
  renderBackendPicker();
  applyModeForDoc();
  $("job").hidden = true;
  state.jobId = null;
  $("live-badge").hidden = !running;
  $("offset-wrap").style.display = "flex";
  await attachLive(st.id, st.video_id);
}

async function attachLive(sessionId, videoId) {
  // m3u8을 직접 넣은 세션에는 임베드할 영상이 없습니다. 그래도 스크립트 패널은
  // 읽을 수 있어야 하므로 플레이어만 건너뜁니다.
  if (videoId) await createPlayer(videoId);
  const es = new EventSource(`/api/live/events/${sessionId}`);
  state.live.es = es;
  es.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "cue") onLiveCue(m);
    else if (m.type === "translation") onLiveTranslation(m);
    else if (m.type === "status") onLiveStatus(m);
  };
  es.onerror = () => {
    // 끝난 세션은 서버가 백로그를 다 보내고 스트림을 닫습니다. 그것은 끊김이
    // 아니라 정상 종료이고, EventSource는 끊기면 알아서 다시 붙으므로 여기서
    // 닫지 않으면 몇 초마다 자막 전체를 다시 받게 됩니다. 진행 중인 세션은
    // 반대로 그 자동 재접속이 필요하니 그대로 둡니다.
    const st = state.live && state.live.state;
    if (st && !LIVE_RUNNING.includes(st)) { es.close(); return; }
    $("lang-status").innerHTML = "라이브 연결 끊김";
  };
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
  // 세션 id로 값을 잡습니다. 같은 방송을 두 번 켜면 영상 id가 겹쳐서, 뒤에서
  // 영상 하나를 고르려다 세션 항목이 잡히던 자리입니다.
  const value = "live:" + sessionId;
  const row = videoRow({
    value, session: sessionId, title: probe.title || "",
    videoId: probe.id || "", meta: "받는 중", live: true, deletable: false,
  });
  row.classList.add("pending");     // 새로고침 전까지의 임시 항목입니다
  box.prepend(row);
  markVideoRow(value);
}

/* Removing the entry on stop was the mismatch: the player went on showing a
 * broadcast the list no longer had. Keep the entry, say the subtitles ended. */
function markLiveStopped() {
  const row = $("video-list").querySelector(".video-row.live.pending");
  if (!row) return;
  row.classList.add("stopped");
  const m = row.querySelector(".vm");
  if (m) m.textContent = "자막 중단";
}

/* The stopped entry stands for what the player is showing. Once the viewer
 * picks something else the player moves on, and so does the entry. */
function dropLiveOption(keepValue) {
  $("video-list").querySelectorAll(".video-row.live.pending").forEach(r => {
    if (r.dataset.value !== keepValue) r.remove();
  });
}

function onLiveCue(m) {
  const live = state.live;
  if (!live) return;

  // A refined line supersedes the finals it absorbed. Drop those from both
  // the cue list and the panel, keeping the one the refine reuses.
  (m.replaces || []).forEach(id => {
    if (id === m.id) return;
    const old = live.byId.get(id);
    if (!old) return;
    const i = state.cues.indexOf(old);
    if (i >= 0) state.cues.splice(i, 1);
    live.byId.delete(id);
    const row = $("script").querySelector(`.line[data-id="${id}"]`);
    if (row) row.remove();
  });

  const existing = live.byId.get(m.id);
  const cue = existing || { translations: {}, id: m.id };
  Object.assign(cue, { start: m.t, end: m.t + 6, text: m.text,
                       lang: m.lang, kind: m.kind, speaker: m.speaker || "",
                       arrived: Date.now() });
  if (!existing) {
    state.cues.push(cue);        // SSE delivers in order; no sort needed
    live.byId.set(m.id, cue);
  }
  const before = live.speakers.size;
  if (cue.speaker) live.speakers.add(cue.speaker);
  state.idx = -1;
  if (before < 2 && live.speakers.size >= 2) {
    // The chips just became meaningful; the lines already on screen need them.
    buildLiveScript();
  } else {
    appendScriptLine(cue);
  }
  renderCue();
}

function buildLiveScript() {
  $("script").textContent = "";
  state.cues.forEach(c => appendScriptLine(c));
}

function onLiveTranslation(m) {
  const cue = state.live && state.live.byId.get(m.id);
  if (!cue) return;
  cue.translations[state.backend] = m.text;
  if (!state.doc.translated) {
    state.doc.translated = true;
    applyModeForDoc();
  }
  const row = $("script").querySelector(`.line[data-id="${cue.id}"]`);
  if (row) refreshScriptRow(row, cue);
  // 번역 한 줄이 붙으면서 이 줄이 높아졌습니다. 바닥을 다시 잡습니다.
  pinScriptToBottom();
  renderCue();
}

function onLiveStatus(m) {
  const el = $("lang-status");
  if (state.live) state.live.state = m.state;
  if (m.state === "error") {
    el.className = "status warn";
    el.textContent = m.error || "라이브 오류";
    stopLive();
    if (m.state === "error") {
      el.className = "status warn";
      el.textContent = m.error || "라이브 오류";
    }
    return;
  }
  // 중단된 세션은 오류가 아닙니다. 수신은 끊겼지만 여기 떠 있는 자막은 진짜로
  // 받아 적은 것이므로, 세션을 접지 않고 왜 멈췄는지만 알립니다.
  if (m.state === "interrupted") {
    el.className = "status warn";
    el.innerHTML = `${LIVE_STATE.interrupted} · ${m.lines || 0}줄까지 남아 있습니다`;
    $("live-badge").hidden = true;
    return;
  }
  el.className = "status";
  const src = m.source_lang || "auto";
  const eng = (m.asr || "").replace(/-Q8_0$|\.gguf$/g, "");
  el.innerHTML = `${LIVE_STATE[m.state] || m.state} · 원본 <b>${src}</b> → <b>${m.viewer_lang}</b>`
    + (eng ? ` · 전사 <b>${esc(eng)}</b>` : "")
    + (m.lines ? ` · ${m.lines}줄` : "");
}

/* "중단" ends the transcription session, not the viewing. Nothing here
 * touches the player: the viewer asked for the subtitles to stop, not for the
 * broadcast to. The cues already received stay in the panel and in
 * state.mode -- they cost nothing and re-reading them is the whole point of
 * the panel. */
/* 화면에서만 손을 뗍니다. 서버 세션은 그대로 두므로 받아 적기가 이어집니다. */
function detachLive() {
  const live = state.live;
  if (!live) return;
  if (live.es) live.es.close();
  state.live = null;
  hideLiveNotice();
  $("live-badge").hidden = true;
  $("offset-wrap").style.display = "";
}

function stopLive() {
  const live = state.live;
  if (!live) return;
  if (live.es) live.es.close();
  hideLiveNotice();
  fetch("/api/live/stop", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: live.id }),
  }).catch(() => {});
  state.live = null;
  $("live-badge").hidden = true;
  $("offset-wrap").style.display = "";
  markLiveStopped();
  const el = $("lang-status");
  el.className = "status";
  el.textContent = "자막 중단됨 · 방송은 계속 재생됩니다";
}

/* 목록의 행에서 부릅니다. 예전에는 헤더의 단추가 "지금 열려 있는 것"만
 * 지울 수 있었는데, 그러면 목록에서 보는 것과 지워지는 것이 어긋납니다. */
async function deleteVideo(id, title) {
  if (!confirm(`'${(title || "").slice(0, 50)}' 전사를 삭제할까요?\n`
               + "내려받은 오디오도 함께 지웁니다.")) return;
  const res = await (await fetch("/api/video/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  })).json();
  if (res.error) { alert(res.error); return; }
  // 지운 것이 지금 보고 있는 것이면 다른 것을 엽니다. 아니면 목록만
  // 다시 그리고 화면은 그대로 둡니다.
  const open = state.doc && state.doc.id === id && !isLiveDoc();
  const list = await (await fetch("/api/videos")).json();
  if (open && !list.length) { location.reload(); return; }
  await refreshVideoList(open ? list[0].id : undefined);
}

/* 목록은 <select>가 아니라 행으로 그립니다.
 *
 * 고르기만 하던 때는 select로 충분했지만, 지우기와 상태 표시가 같은 자리에
 * 있어야 하고 제목도 한 줄로 잘리지 않아야 합니다. 삭제 단추가 헤더에
 * 따로 있으면 "지금 열려 있는 것"만 지울 수 있어, 목록에서 보이는 것과
 * 지워지는 것이 어긋납니다. */
function videoRow({ value, session, title, meta, live, stopped, deletable, videoId }) {
  const row = document.createElement("div");
  row.className = "video-row" + (live ? " live" : "") + (stopped ? " stopped" : "");
  row.dataset.value = value;
  if (session) row.dataset.session = session;
  row.dataset.title = title;

  // 제목만 있는 목록에서는 어느 방송인지 한눈에 오지 않습니다. 유튜브가
  // 주는 썸네일을 그대로 씁니다 -- 플레이어를 이미 임베드하고 있으므로
  // 브라우저는 어차피 구글과 통신합니다.
  const th = document.createElement("img");
  th.className = "vth";
  th.alt = "";
  th.decoding = "async";
  // `loading="lazy"` 는 쓰지 않습니다. 이 요소는 DOM에 붙기 전에 src를
  // 받는데, 그러면 브라우저가 지연을 풀 시점을 제대로 잡지 못해 22장 중
  // 한 장만 뜨고 나머지는 매달려 있었습니다. 한 장이 10KB 남짓이라
  // 미루어서 얻는 것도 없습니다.
  if (videoId) th.src = `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/mqdefault.jpg`;
  else th.classList.add("blank");
  // 못 받아도 자리는 남깁니다. 줄 높이가 들쭉날쭉하면 목록이 읽기 나빠집니다.
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

  if (deletable) {
    const del = document.createElement("button");
    del.className = "vdel";
    del.title = "이 전사를 삭제합니다";
    del.textContent = "🗑";
    del.addEventListener("click", (e) => {
      e.stopPropagation();          // 지우려다 열리면 안 됩니다
      deleteVideo(value, title);
    });
    row.appendChild(del);
  } else {
    row.appendChild(document.createElement("span"));
  }

  row.addEventListener("click", () => openFromList(value));
  return row;
}

function openFromList(value) {
  const row = $("video-list").querySelector(`.video-row[data-value="${CSS.escape(value)}"]`);
  const sid = row && row.dataset.session;
  markVideoRow(value);
  if (sid) { resumeLive(sid); return; }
  stopLive();
  dropLiveOption(value);
  loadVideo(value);
}

function markVideoRow(value) {
  $("video-list").querySelectorAll(".video-row").forEach(r =>
    r.classList.toggle("on", r.dataset.value === value));
  const row = value && $("video-list").querySelector(
    `.video-row[data-value="${CSS.escape(value)}"]`);
  setNowTitle(row ? row.dataset.title : null);
}

/* 위쪽 막대는 "지금 무엇을 보고 있는가"를 답하는 자리입니다. */
function setNowTitle(title) {
  const el = $("now-title");
  el.textContent = title || "영상을 고르거나 추가하십시오";
  el.classList.toggle("empty", !title);
  el.title = title || "";
}

async function refreshVideoList(selectId) {
  const list = await (await fetch("/api/videos")).json();
  const sessions = await (await fetch("/api/live/sessions")).json();
  const box = $("video-list");
  const current = box.querySelector(".video-row.on");
  const keep = current && current.dataset.value;
  box.textContent = "";

  // 라이브 세션에는 큐 파일이 없어서, 예전에는 탭을 닫으면 그 방송의 자막이
  // 통째로 사라졌습니다. 이제 서버가 들고 있으므로 목록에 올려 다시 엽니다.
  // 한 줄도 못 받은 세션은 열어 봐야 볼 것이 없으니 뺍니다.
  sessions.filter(s => s.cues).forEach(s => {
    const running = LIVE_RUNNING.includes(s.state);
    box.appendChild(videoRow({
      value: "live:" + s.id, session: s.id,
      title: s.title || s.url, videoId: s.video_id || "",
      meta: `${s.cues}줄` + (running ? "" : `  ·  ${LIVE_STATE[s.state] || s.state}`),
      live: true, stopped: !running, deletable: false,
    }));
  });
  list.forEach(v => {
    const mins = v.duration ? `${Math.round(v.duration / 60)}분` : "";
    box.appendChild(videoRow({
      value: v.id, title: v.title, videoId: v.id,
      meta: [v.source_lang + (v.translated ? `→${v.viewer_lang}` : ""), mins]
        .filter(Boolean).join("  ·  "),
      deletable: true,
    }));
  });
  if (!box.children.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "아직 없습니다. 「＋ 추가」로 주소를 넣으십시오.";
    box.appendChild(e);
  }

  if (selectId && list.some(v => v.id === selectId)) {
    markVideoRow(selectId);
    await loadVideo(selectId);
  } else if (keep) {
    markVideoRow(keep);
  }
}

async function cancelJob() {
  if (!state.jobId) return;
  await fetch("/api/job/cancel", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: state.jobId }),
  });
  $("job-cancel").disabled = true;
  document.querySelector(".job-label").textContent = "중단하는 중…";
}


/* 실패를 위쪽 막대에 띄웁니다.
 *
 * 예전에는 hidden을 풀지 않아서, 진행 상자가 이미 떠 있을 때만 보였습니다.
 * 주소를 잘못 넣는 것처럼 시작도 못 한 실패는 그래서 조용히 묻혔습니다.
 * 라벨도 "재번역 실패"로 고정되어 있었는데, 이 함수는 전사·주소 해석
 * 실패에도 쓰입니다. */
function jobError(msg) {
  const box = $("job");
  box.hidden = false;
  box.classList.add("error");
  box.querySelector(".job-label").textContent = "실패";
  $("job-fill").style.width = "0%";
  $("job-count").textContent = msg;
  $("job-source").textContent = "";
  $("job-cancel").hidden = true;
  setTimeout(() => { box.hidden = true; $("job-cancel").hidden = false; }, 8000);
}


(async function init() {
  restore(); bind();
  await loadBackends();
  const list = await (await fetch("/api/videos")).json();
  const sessions = await (await fetch("/api/live/sessions")).json();
  if (!list.length && !sessions.some(s => s.cues)) {
    await refreshVideoList();       // 빈 목록 안내를 그립니다
    setLibrary(false);              // 처음 온 사람에게는 목록을 펼쳐 둡니다
    return;
  }
  // 서버는 멀쩡한데 탭만 새로고침한 경우입니다. 보고 있던 방송으로 그대로
  // 돌아갑니다 -- 그 자막을 다시 만들 방법은 없으니까요.
  const running = sessions.find(s => LIVE_RUNNING.includes(s.state));
  await refreshVideoList(running ? null : (list[0] || {}).id);
  if (running) {
    markVideoRow("live:" + running.id);
    await resumeLive(running.id);
  }
})();
