/* 확장 팝업. 지금 이 탭에 대해 할 일을 답합니다.
 *
 *   - 이미 받아 둔 자막 중 무엇을 얹을까
 *   - 이 탭에서 새로 받아 적기 (주소로, 또는 이 탭 소리로)
 *   - 자막 모양
 *
 * 목록 관리·엔진 설정·스크립트 편집은 여기 없습니다. mimiwatch 페이지가
 * 그대로 맡습니다 -- 그것까지 팝업에 지으면 두 벌이 됩니다.
 *
 * 새로 시작하는 것은 여기 둡니다. 확장은 이미 어느 탭인지 알고 있어서
 * 주소를 다시 붙여 넣을 이유가 없고, 멤버십 전용 방송은 이 탭의 소리로만
 * 받을 수 있기 때문입니다.
 */
const $ = (id) => document.getElementById(id);

const send = (msg) => new Promise((r) => chrome.runtime.sendMessage(msg, r));
const toTab = (tabId, msg) =>
  new Promise((r) => chrome.tabs.sendMessage(tabId, msg, () => r(chrome.runtime.lastError ? null : true)));

let tabId = null;
/* 이 탭이 유튜브인가. init 밖에 두는 것은 언어가 바뀌었을 때 「유튜브 탭에서
 * 열어야」를 그 언어로 다시 적기 위해서입니다 -- 그 문구는 팝업을 여는 순간
 * 한 번 그려지고 그대로 남습니다. */
let onYouTube = false;
let prefs = { mode: "both", showPrev: true, size: 30, dim: 0.55, offset: 0,
              panel: false };
/* 새 세션을 시작할 때 쓰는 값. 자막 모양(prefs)과 나눠 둡니다 -- 저쪽은
 * 지금 보이는 것을 바꾸고, 이쪽은 다음에 시작할 것을 정합니다. */
/* 정제는 **꺼 둔 채로 시작합니다.** mimiwatch 페이지의 기본값과 다릅니다.
 * 저쪽은 녹화본도 다루지만 확장은 라이브만 시작하고, 라이브에서는 정제가
 * 대개 손해입니다 -- 발화 한 무리가 끝나기를 2초 기다렸다 합쳐서 다시
 * 받아 적으므로, 말이 빠르게 오가면 자막이 늦게 자리를 잡고 이미 읽은 줄이
 * 통째로 바뀝니다. */
let start = { lang: "", genre: "general", refine: false, profile: "broadcast",
              // 번역 대상 언어. 예전에는 "ko"로 박혀 있어 한국어 사용자만
              // 확장을 쓸 수 있었습니다. mimiwatch 페이지의 「내 언어」와 같은 값.
              viewerLang: "ko" };
const SKEY = "startPrefs";

/* 화면에 쓸 언어는 서버가 들고 있습니다(설정의 `ui_lang`). 팝업이 뜨는 순간에는
 * 아직 모르므로 표는 영어로 시작하고, 첫 조회가 돌아온 뒤에 정해집니다 -- 그래서
 * 우리가 적는 문구는 언어가 정해진 다음에 그려야 하고, 나중에 바뀌면(다른 서버로
 * 옮기는 등) 다시 그려야 합니다.
 *
 * 알아낸 값은 저장해 둡니다. 배경 워커와 content script 에는 서버에 언어를 물을
 * 자리가 없는데(하나는 화면이 없고, 하나는 서버에 직접 닿지 않습니다) 그쪽 문구도
 * 같은 언어여야 합니다. */
const LANG_KEY = "uiLang";

async function useLang(code) {
  // 모르는 코드(빈 문자열 포함)면 i18n 이 브라우저의 짐작으로 갑니다. 표를
  // 바꾸는 것은 이 첫 줄뿐이라 부르는 쪽이 기다릴 것은 없습니다.
  const lang = MW_I18N.setLang(code || "");
  // 같은 값을 다시 적지 않습니다. storage 는 값이 그대로여도 변화 알림을
  // 띄우고, 그 알림이 붙어 있는 유튜브 탭들과 잠든 워커를 깨웁니다.
  const got = await chrome.storage.local.get(LANG_KEY);
  if (got[LANG_KEY] !== lang) chrome.storage.local.set({ [LANG_KEY]: lang });
}

/* 읽어 오는 중인가. 그동안은 언어가 바뀌어도 다시 그리지 않습니다. */
let loading = false;

/* 언어가 바뀌면 우리가 적은 것을 다시 적습니다. 붙박이 문구는 i18n 이 손보지만
 * 고르개 항목·상태줄·단추 이름은 여기서 지은 것입니다. 읽어 오는 중에는
 * 건너뜁니다 -- loadFromServer 가 곧 전부 다시 그립니다. */
MW_I18N.onChange(() => {
  if (loading) return;
  if (!onYouTube) fail(t("popup.errNotYouTube"));
  syncProfileHint();
  syncResumeButton();
  syncHideButton();
  refreshState();
  refillPicker();
});

function fail(text) {
  $("err").textContent = text;
  $("err").hidden = !text;
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabId = tab && tab.id;
  onYouTube = !!(tab && /^https:\/\/www\.youtube\.com\//.test(tab.url || ""));
  if (!onYouTube) fail(t("popup.errNotYouTube"));

  const b = await send({ type: "base" });
  $("base").value = (b && b.data) || "http://localhost:8900";

  const got = await chrome.storage.local.get("overlayPrefs");
  if (got.overlayPrefs) Object.assign(prefs, got.overlayPrefs);
  $("size").value = prefs.size;
  $("dim").value = Math.round(prefs.dim * 100);
  $("offset").value = prefs.offset;
  $("offset-val").textContent = (+prefs.offset).toFixed(1) + "s";
  $("show-prev").checked = !!prefs.showPrev;
  $("panel").checked = !!prefs.panel;
  syncModes();

  const sp = await chrome.storage.local.get(SKEY);
  if (sp[SKEY]) Object.assign(start, sp[SKEY]);

  await loadFromServer();
  // 유튜브 탭이 아니면 시작할 것도 없습니다.
  $("start-box").classList.toggle("busy", !onYouTube);
}

/* 서버에서 읽어 오는 것 전부. 처음 열 때와 「서버」 주소를 바꿀 때 부릅니다.
 *
 * 예전에는 주소를 바꾸면 고르개만 다시 채웠습니다. 서버가 8900 에 없어 처음에
 * 실패했으면 장르·콘텐츠 유형은 빈 채로 남고 변화 알림도 옛 주소를 듣고
 * 있어서, 주소를 고쳐도 팝업은 여전히 쓸 수 없었습니다. */
async function loadFromServer() {
  loading = true;
  $("pick").length = 1;
  // 장르·콘텐츠 유형을 **먼저** 읽습니다. 그 답에 화면 언어가 실려 오므로,
  // 고르개 항목처럼 우리가 문구를 지어 붙이는 것은 그 뒤에 그려야 합니다 --
  // 순서가 반대였을 때는 팝업을 열 때마다 목록만 영어로 잠깐 남았습니다.
  await fillChoices();
  await fillPicker();
  // 고르개를 채운 **뒤에** 값을 앉힙니다. 비어 있는 select 에 value 를 넣으면
  // 그냥 버려집니다 -- 그래서 매번 처음으로 되돌아가 보였습니다.
  $("lang").value = start.lang || "";
  $("genre").value = start.genre || "general";
  if (!$("genre").value) $("genre").selectedIndex = 0;
  $("refine").checked = !!start.refine;
  $("profile").value = start.profile || "broadcast";
  if (!$("profile").value) $("profile").selectedIndex = 0;
  $("viewer").value = start.viewerLang || "ko";
  if (!$("viewer").value) $("viewer").selectedIndex = 0;
  syncProfileHint();
  await refreshState();
  await syncHideButton();
  watchServer();
  loading = false;
}

/* 팝업이 열려 있는 동안 서버의 변화(새 세션·상태·영상)를 받아 고르개를 다시
 * 채웁니다. 팝업은 열 때마다 목록을 새로 읽으므로 대개는 충분하지만, 열어 둔
 * 채로 mimiwatch 페이지나 다른 탭에서 방송을 시작하면 그 세션이 보이지
 * 않았습니다. 확장 페이지는 host_permissions 덕에 CORS 없이 서버에 붙습니다. */
let serverEs = null;
let refillTimer = null;

function watchServer() {
  if (serverEs) { serverEs.close(); serverEs = null; }
  try {
    serverEs = new EventSource($("base").value.replace(/\/+$/, "") + "/api/events");
  } catch (_) {
    return;
  }
  serverEs.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch (_) { return; }
    if (m.type !== "session" && m.type !== "video") return;
    // 자막 한 줄마다 오므로 잠깐 모아서 한 번에 다시 채웁니다.
    clearTimeout(refillTimer);
    refillTimer = setTimeout(refillPicker, 500);
  };
}

async function refillPicker() {
  const was = $("pick").value;
  $("pick").length = 1;
  await fillPicker();                       // 서버가 아는 것과 이 탭이 보는 것으로 다시
  if (!$("pick").value && was && [...$("pick").options].some((o) => o.value === was)) {
    $("pick").value = was;                  // 고르고 있던 것은 그대로
  }
  await syncHideButton();
  refreshState();
}

/* 장르와 콘텐츠 유형은 서버가 들고 있습니다. 팝업에 붙박이로 적어 두면
 * 서버에서 늘리거나 값을 손볼 때마다 어긋납니다 -- 특히 콘텐츠 유형은
 * 실제로 몇 초에 끊을지가 그 표에 들어 있습니다. */
let profiles = [];
/* 고르개에 올린 세션들의 상태. 「이어받기」를 멈춘 방송에만 살리는 데 씁니다. */
let sessionInfo = {};
const RUNNING = ["starting", "loading", "running"];

async function fillChoices() {
  const r = await send({ type: "backends" });
  // 화면 언어도 이 답에 실려 옵니다. 서버에 닿지 못했으면 브라우저의 짐작으로
  // 갑니다 -- 그때 뜨는 오류 문구도 사람이 읽을 언어여야 합니다.
  useLang(r && r.ok ? r.data.ui_lang : "");
  if (!r || !r.ok) return;
  const g = $("genre");
  g.length = 0;                             // 주소를 바꿔 다시 읽을 때 겹치지 않게
  for (const x of (r.data.genres || [])) g.append(new Option(x.label || x.id, x.id));
  if (!g.length) g.append(new Option(t("popup.genreGeneral"), "general"));

  profiles = r.data.live_profiles || [];
  const p = $("profile");
  p.length = 0;
  for (const x of profiles) p.append(new Option(x.label || x.id, x.id));
  if (!p.length) p.append(new Option(t("popup.profileBroadcast"), "broadcast"));
}

/* 고른 유형이 실제로 몇 초에 끊는지 적어 둡니다. 「일반 방송」이 4초라는
 * 것을 모르면 왜 자막이 잘게 끊기는지 알 수 없습니다. */
function syncProfileHint() {
  const x = profiles.find((p) => p.id === $("profile").value);
  $("profile-hint").textContent = x
    ? t("popup.hintProfile", { max: x.max_speech, min: x.min_silence })
    : t("popup.hintProfileNone");
}

/* 서버가 들고 있는 것을 한 목록으로. 라이브 세션이 위, 녹화본이 아래입니다 --
 * 지금 얹고 싶은 것은 대개 방금 받아 적은 쪽입니다. */
async function fillPicker() {
  const sel = $("pick");
  const [s, v] = await Promise.all([send({ type: "sessions" }), send({ type: "videos" })]);
  if (!s || !s.ok) {
    fail(t("popup.errNoServer",
           { error: (s && s.error) || t("popup.errNoResponse") }));
    return;
  }
  fail("");
  sessionInfo = {};
  for (const x of (s.data || []).filter((x) => x.cues || RUNNING.includes(x.state))) {
    sessionInfo[x.id] = x;
    const running = RUNNING.includes(x.state);
    // 멈춘 것은 그렇게 적어 둡니다. 「이어받기」가 무엇을 가리키는지 보이게요.
    // 꼬리만 따로 잇지 않고 항목 전체를 한 문구로 둡니다 -- 말 순서가 언어마다
    // 다르므로 반쪽을 붙여 짓는 것은 한 언어에서만 맞습니다.
    const key = running ? "popup.pickLines"
      : x.stopped_by === "ended" ? "popup.pickLinesEnded" : "popup.pickLinesStopped";
    sel.append(new Option(t(key, { title: x.title || x.id, n: x.cues || 0 }),
                          "live:" + x.id));
  }
  for (const x of (v && v.data) || []) {
    sel.append(new Option(t("popup.pickLines", { title: x.title || x.id, n: x.cues }),
                          x.id));
  }
  const now = await send({ type: "watching", tabId });
  if (now && now.data) sel.value = now.data;
  syncResumeButton();
}

/* 고른 것이 멈춘 방송이면 시작 단추가 이어받기로 바뀝니다. 끝난 방송은 이어받을 것이
 * 없습니다 -- 전체 영상 전사는 mimiwatch 페이지에서 합니다. */
function syncResumeButton() {
  const v = $("pick").value || "";
  const st = v.startsWith("live:") ? sessionInfo[v.slice(5)] : null;
  const can = !!st && !RUNNING.includes(st.state) && st.stopped_by !== "ended";
  // 멈춘 방송을 골라 두었으면 「새로 받아 적기」 단추 **둘 다** 이어받기가 됩니다 -- 출처는 바꿔
  // 이어받을 수 있습니다(주소로 받던 방송이 멤버십 전용으로 바뀌면 탭 소리로). 예전에는 그
  // 상태로 「주소로」를 누르면 같은 방송이 새 세션으로 갈라졌습니다.
  $("start-url").textContent = t(can ? "popup.resumeFromUrl" : "popup.fromUrl");
  $("start-tab").textContent = t(can ? "popup.resumeFromTab" : "popup.fromTab");
  $("start-url-cookies").textContent =
    t(can ? "popup.resumeWithCookies" : "popup.fromUrlWithCookies");
  // 「수신이 끊겨 멈춤」은 문구 뒤에 덧붙이지 않고 그 경우의 문구를 따로 둡니다.
  $("start-hint").textContent = can
    ? t(st.stopped_by === "stream" ? "popup.hintResumePickStream" : "popup.hintResumePick")
    : st && st.stopped_by === "ended" ? t("popup.hintEnded")
    : t("popup.hintStart");
}

async function refreshState() {
  if (!tabId) return;
  chrome.tabs.sendMessage(tabId, { type: "state" }, (r) => {
    if (chrome.runtime.lastError || !r) {
      $("state").textContent = t("popup.stateNotAttached");
      return;
    }
    if (!r.mounted) { $("state").textContent = t("popup.statePickToOverlay"); return; }
    // 「안 보인다」는 여러 가지입니다. 붙었는지, 크기가 있는지, 그릴 자막이
    // 있는지를 구별해 적습니다 -- 그래야 어디를 봐야 할지 알 수 있습니다.
    const bits = [t("popup.stateCues", { n: r.cues })];
    bits.push(t(r.live ? (r.receiving ? "popup.stateReceiving" : "popup.stateStreamEnded")
                       : "popup.stateVod"));
    if (!r.player) bits.push(t("popup.stateNoPlayer"));
    else if (!r.box || !r.box.w) bits.push(t("popup.stateNoRoom"));
    else if (!r.text) bits.push(t(r.mode === "off" ? "popup.stateOff" : "popup.stateNoCue"));
    if (r.stalled) bits.push(t("popup.stateStalled"));
    if (!r.ticking) bits.push(t("popup.stateNoClock"));
    if (r.panel) bits.push(t(r.panelUp ? "popup.statePanelUp" : "popup.statePanelNoRoom"));
    $("state").textContent = bits.join(" · ");
  });
}

function syncModes() {
  document.querySelectorAll("[data-mode]").forEach((b) =>
    b.classList.toggle("on", b.dataset.mode === prefs.mode));
}

async function pushPrefs() {
  await chrome.storage.local.set({ overlayPrefs: prefs });
  if (tabId) await toTab(tabId, { type: "prefs", prefs });
}

$("pick").addEventListener("change", async (e) => {
  syncResumeButton();
  const r = await send({ type: "watch", tabId, value: e.target.value });
  if (r && !r.ok) { fail(r.error || t("popup.errAttach")); return; }
  // 페이지가 아직 우리 것을 들고 있지 않아 새로고침했습니다. 조용히 하면
  // 화면이 저 혼자 다시 뜬 것처럼 보이므로 그렇다고 적어 둡니다.
  if (r && r.reloaded) $("state").textContent = t("popup.stateReloaded");
  await syncHideButton();
  setTimeout(refreshState, r && r.reloaded ? 1800 : 600);
});

document.querySelectorAll("[data-mode]").forEach((b) =>
  b.addEventListener("click", () => { prefs.mode = b.dataset.mode; syncModes(); pushPrefs(); }));

$("size").addEventListener("input", (e) => { prefs.size = +e.target.value; pushPrefs(); });
$("dim").addEventListener("input", (e) => { prefs.dim = +e.target.value / 100; pushPrefs(); });
$("offset").addEventListener("input", (e) => {
  prefs.offset = +e.target.value;
  $("offset-val").textContent = prefs.offset.toFixed(1) + "s";
  pushPrefs();
});
$("show-prev").addEventListener("change", (e) => { prefs.showPrev = e.target.checked; pushPrefs(); });
$("panel").addEventListener("change", (e) => {
  prefs.panel = e.target.checked; pushPrefs(); setTimeout(refreshState, 500);
});
$("reset-pos").addEventListener("click", () => { prefs.pos = null; pushPrefs(); });
/* 두 가지 일을 한 단추가 하고 있었습니다. 「치우기」라고 적어 두고 화면에서만
 * 내렸는데, 그것을 누른 사람은 받아 적기가 끝난 줄 알았습니다. 서버에서는
 * 계속 돌고 있었고요. 나눕니다.
 *
 * 내리는 쪽은 **토글**입니다. 내리기만 하고 되돌릴 길을 주지 않으면 목록에서
 * 다시 찾아 고르는 수밖에 없는데, 세션이 스무 개쯤 쌓이면 그것이 일입니다.
 * 고르개는 「무엇을」에 답하고, 이 단추는 「지금 화면에 있나」에 답합니다 --
 * 그래서 내려도 고르개는 그대로 둡니다. */
async function syncHideButton() {
  const now = await send({ type: "watching", tabId });
  const on = !!(now && now.data);
  $("hide").textContent = t(on ? "popup.hide" : "popup.show");
  $("hide").title = t(on ? "popup.hideTitle" : "popup.showTitle");
  // 얹을 것이 없으면 누를 것도 없습니다.
  $("hide").disabled = !on && !$("pick").value;
  return on;
}

$("hide").addEventListener("click", async () => {
  const on = !!(await send({ type: "watching", tabId }))?.data;
  await send({ type: "watch", tabId, value: on ? "" : $("pick").value });
  await syncHideButton();
  setTimeout(refreshState, 400);
});

$("stop").addEventListener("click", async () => {
  const value = $("pick").value;
  const id = value.startsWith("live:") ? value.slice(5) : "";
  if (!id) { fail(t("popup.errNotSession")); return; }
  $("stop").disabled = true;
  const r = await send({ type: "stopSession", sessionId: id, tabId });
  $("stop").disabled = false;
  if (r && !r.ok) { fail(r.error || t("popup.errStop")); return; }
  fail("");
  // 화면에서도 내립니다. 받아 적기가 끝났는데 자막만 떠 있으면 아직 도는
  // 것처럼 보입니다. 쌓인 것은 서버에 그대로 남아 다시 고를 수 있습니다.
  //
  // 여기서는 고르개도 비웁니다. 내리기와 달리 그 세션은 이제 받지 않으므로
  // 「다시 얹기」가 가리킬 것이 없습니다.
  await send({ type: "watch", tabId, value: "" });
  $("pick").value = "";
  await syncHideButton();
  $("start-hint").textContent = t("popup.hintStopped");
  $("pick").length = 1;
  await fillPicker();
  setTimeout(refreshState, 400);
});
/* 멈춘 세션을 같은 세션으로 이어 붙입니다. 라이브는 사용자가 잠깐 멈추고
 * 돌아오는 일이 잦고, 서버가 죽어 끊기기도 합니다. 그때 새 세션을 시작하면
 * 자막이 두 벌로 갈리므로, 확장에서도 이어받을 수 있어야 합니다. 탭 소리로 받던
 * 세션이면 이 탭의 소리를 다시 잡습니다 -- 서버는 그 소리를 되감을 수 없습니다. */
/* `source` 가 비어 있으면 저장된 출처 그대로, "tab"/"hls" 면 그 출처로 바꿔 이어받습니다. */
async function resumePicked(source, tab) {
  const value = $("pick").value;
  const id = value.startsWith("live:") ? value.slice(5) : "";
  if (!id) return;
  $("start-box").classList.add("busy");
  $("start-hint").textContent = t("popup.hintResuming");
  fail("");
  const r = await send({ type: "resumeSession", sessionId: id, tabId, source: source || "",
                         url: tab ? tab.url : "" });
  $("start-box").classList.remove("busy");
  if (!r || !r.ok) {
    fail((r && r.error) || t("popup.errResume"));
    $("start-hint").textContent = "";
    syncResumeButton();
    return;
  }
  $("start-hint").textContent =
    t(r.source === "tab" ? "popup.hintResumedTab" : "popup.hintResumedUrl");
  $("pick").length = 1;
  await fillPicker();
  $("pick").value = "live:" + id;
  syncResumeButton();
  await syncHideButton();
  setTimeout(refreshState, 800);
}

/* ---------- 이 탭에서 새로 시작 ---------- */

/* 고르개의 세션이 멈춘 것이면 새로 시작하는 대신 그 세션에 이어 붙입니다. 시작 방식이
 * 곧 이어받는 출처입니다 -- 저장된 출처와 달라도 됩니다. */
function pickedResumable() {
  const v = $("pick").value || "";
  const st = v.startsWith("live:") ? sessionInfo[v.slice(5)] : null;
  if (!st || RUNNING.includes(st.state) || st.stopped_by === "ended") return null;
  return st;
}

async function startWith(type, opts = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  if (opts.cookies) {
    // 멤버십 전용 방송: 지금 이 브라우저의 로그인 쿠키를 서버에 넘긴 뒤 주소로 갑니다. 켜 두는
    // 것이 아니라 이 한 번만입니다 -- 서버에 남은 파일은 mimiwatch 페이지에서 지웁니다.
    $("start-hint").textContent = t("popup.hintPushingCookies");
    const c = await send({ type: "pushCookies" });
    if (!c || !c.ok) { fail((c && c.error) || t("popup.errCookies")); $("start-hint").textContent = ""; return; }
  }
  if (pickedResumable()) {
    await resumePicked(type === "startCapture" ? "tab" : "hls", tab);
    return;
  }
  $("start-box").classList.add("busy");
  fail("");
  $("start-hint").textContent = t("popup.hintStarting");
  const r = await send({
    type, tabId: tab.id, url: tab.url,
    // 탭 제목이 곧 세션 이름입니다. 확장은 크롬이 감추는 그 제목을
    // 그냥 읽을 수 있습니다 -- 페이지 쪽에서는 트랙 label 이 불투명한
    // 식별자라 「탭 오디오」로만 남았습니다.
    title: (tab.title || "").replace(/\s+-\s+YouTube$/, ""),
    lang: start.lang || null, genre: start.genre || "general",
    refine: !!start.refine, profile: start.profile || "broadcast",
    viewerLang: start.viewerLang || "ko",
  });
  $("start-box").classList.remove("busy");
  if (!r || !r.ok) {
    fail((r && r.error) || t("popup.errStart"));
    $("start-hint").textContent = "";
    return;
  }
  $("start-hint").textContent = t(r.reloaded
    ? "popup.hintStartedReloaded"
    : r.skippedReload
      // 탭 소리를 잡는 중이라 새로고침하지 않았습니다. 그러면 자막이
      // 화면에 붙지 않으므로, 무엇을 해야 하는지 적어 둡니다.
      ? "popup.hintStartedNeedsReload"
      : "popup.hintStarted");
  $("pick").length = 1;
  await fillPicker();
  $("pick").value = "live:" + r.id;
  await syncHideButton();
  setTimeout(refreshState, 800);
}

function saveStart() { chrome.storage.local.set({ [SKEY]: start }); }
$("lang").addEventListener("change", (e) => { start.lang = e.target.value; saveStart(); });
$("genre").addEventListener("change", (e) => { start.genre = e.target.value; saveStart(); });
$("refine").addEventListener("change", (e) => { start.refine = e.target.checked; saveStart(); });
$("profile").addEventListener("change", (e) => {
  start.profile = e.target.value; saveStart(); syncProfileHint();
});
$("viewer").addEventListener("change", (e) => { start.viewerLang = e.target.value; saveStart(); });

$("start-url").addEventListener("click", () => startWith("startUrl"));
$("start-url-cookies").addEventListener("click", () => startWith("startUrl", { cookies: true }));
$("start-tab").addEventListener("click", () => startWith("startCapture"));

/* 서버 주소를 바꾸면 전부 다시 읽습니다. 배경 워커는 저장된 주소를 요청마다
 * 읽고, 붙어 있는 유튜브 탭들은 배경이 새 주소로 다시 붙게 합니다. */
$("base").addEventListener("change", async (e) => {
  const base = e.target.value.trim().replace(/\/+$/, "") || "http://localhost:8900";
  e.target.value = base;
  await send({ type: "setBase", base });
  fail("");
  await loadFromServer();
});

init();
