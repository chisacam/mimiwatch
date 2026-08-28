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
let prefs = { mode: "both", showPrev: true, size: 30, dim: 0.55, offset: 0,
              panel: false };
/* 새 세션을 시작할 때 쓰는 값. 자막 모양(prefs)과 나눠 둡니다 -- 저쪽은
 * 지금 보이는 것을 바꾸고, 이쪽은 다음에 시작할 것을 정합니다. */
/* 정제는 **꺼 둔 채로 시작합니다.** mimiwatch 페이지의 기본값과 다릅니다.
 * 저쪽은 녹화본도 다루지만 확장은 라이브만 시작하고, 라이브에서는 정제가
 * 대개 손해입니다 -- 발화 한 무리가 끝나기를 2초 기다렸다 합쳐서 다시
 * 받아 적으므로, 말이 빠르게 오가면 자막이 늦게 자리를 잡고 이미 읽은 줄이
 * 통째로 바뀝니다. */
let start = { lang: "", genre: "general", refine: false, profile: "broadcast" };
const SKEY = "startPrefs";

function fail(text) {
  $("err").textContent = text;
  $("err").hidden = !text;
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabId = tab && tab.id;
  const onYouTube = tab && /^https:\/\/www\.youtube\.com\//.test(tab.url || "");
  if (!onYouTube) fail("유튜브 탭에서 열어야 자막을 얹을 수 있습니다.");

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

  await fillPicker();
  await fillChoices();
  // 고르개를 채운 **뒤에** 값을 앉힙니다. 비어 있는 select 에 value 를 넣으면
  // 그냥 버려집니다 -- 그래서 매번 처음으로 되돌아가 보였습니다.
  $("lang").value = start.lang || "";
  $("genre").value = start.genre || "general";
  if (!$("genre").value) $("genre").selectedIndex = 0;
  $("refine").checked = !!start.refine;
  $("profile").value = start.profile || "broadcast";
  if (!$("profile").value) $("profile").selectedIndex = 0;
  syncProfileHint();
  await refreshState();
  await syncHideButton();
  // 유튜브 탭이 아니면 시작할 것도 없습니다.
  $("start-box").classList.toggle("busy", !onYouTube);
}

/* 장르와 콘텐츠 유형은 서버가 들고 있습니다. 팝업에 붙박이로 적어 두면
 * 서버에서 늘리거나 값을 손볼 때마다 어긋납니다 -- 특히 콘텐츠 유형은
 * 실제로 몇 초에 끊을지가 그 표에 들어 있습니다. */
let profiles = [];

async function fillChoices() {
  const r = await send({ type: "backends" });
  if (!r || !r.ok) return;
  const g = $("genre");
  for (const x of (r.data.genres || [])) g.append(new Option(x.label || x.id, x.id));
  if (!g.length) g.append(new Option("일반", "general"));

  profiles = r.data.live_profiles || [];
  const p = $("profile");
  for (const x of profiles) p.append(new Option(x.label || x.id, x.id));
  if (!p.length) p.append(new Option("일반 방송", "broadcast"));
}

/* 고른 유형이 실제로 몇 초에 끊는지 적어 둡니다. 「일반 방송」이 4초라는
 * 것을 모르면 왜 자막이 잘게 끊기는지 알 수 없습니다. */
function syncProfileHint() {
  const x = profiles.find((p) => p.id === $("profile").value);
  $("profile-hint").textContent = x
    ? `발화가 ${x.max_speech}초를 넘으면 끊습니다. 쉼은 ${x.min_silence}초.`
    : "발화를 몇 초에 끊을지 정합니다.";
}

/* 서버가 들고 있는 것을 한 목록으로. 라이브 세션이 위, 녹화본이 아래입니다 --
 * 지금 얹고 싶은 것은 대개 방금 받아 적은 쪽입니다. */
async function fillPicker() {
  const sel = $("pick");
  const [s, v] = await Promise.all([send({ type: "sessions" }), send({ type: "videos" })]);
  if (!s || !s.ok) { fail("서버에 닿지 못했습니다: " + ((s && s.error) || "응답 없음")); return; }
  fail("");
  for (const x of (s.data || []).filter((x) => x.cues)) {
    sel.append(new Option(`${x.title || x.id} · ${x.cues}줄`, "live:" + x.id));
  }
  for (const x of (v && v.data) || []) {
    sel.append(new Option(`${x.title || x.id} · ${x.cues}줄`, x.id));
  }
  const now = await send({ type: "watching", tabId });
  if (now && now.data) sel.value = now.data;
}

async function refreshState() {
  if (!tabId) return;
  chrome.tabs.sendMessage(tabId, { type: "state" }, (r) => {
    if (chrome.runtime.lastError || !r) {
      $("state").textContent = "이 탭에는 아직 붙지 않았습니다.";
      return;
    }
    if (!r.mounted) { $("state").textContent = "고르면 이 탭에 얹습니다."; return; }
    // 「안 보인다」는 여러 가지입니다. 붙었는지, 크기가 있는지, 그릴 자막이
    // 있는지를 구별해 적습니다 -- 그래야 어디를 봐야 할지 알 수 있습니다.
    const bits = [`자막 ${r.cues}줄`];
    bits.push(r.live ? (r.receiving ? "받는 중" : "종료된 방송") : "녹화본");
    if (!r.player) bits.push("플레이어 못 찾음");
    else if (!r.box || !r.box.w) bits.push("화면에 자리 없음");
    else if (!r.text) bits.push(r.mode === "off" ? "자막 끔" : "지금 구간에 자막 없음");
    if (!r.ticking) bits.push("시계 멈춤");
    if (r.panel) bits.push(r.panelUp ? "자막 내역 세움" : "자리 못 찾음");
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
  const r = await send({ type: "watch", tabId, value: e.target.value });
  if (r && !r.ok) { fail(r.error || "붙이지 못했습니다"); return; }
  // 페이지가 아직 우리 것을 들고 있지 않아 새로고침했습니다. 조용히 하면
  // 화면이 저 혼자 다시 뜬 것처럼 보이므로 그렇다고 적어 둡니다.
  if (r && r.reloaded) $("state").textContent = "페이지를 새로고침해 얹었습니다.";
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
  $("hide").textContent = on ? "화면에서 내리기" : "화면에 다시 얹기";
  $("hide").title = on
    ? "화면에서만 내립니다. 받아 적기는 계속됩니다"
    : "고른 것을 이 탭에 다시 얹습니다";
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
  if (!id) { fail("받아 적는 중인 세션이 아닙니다."); return; }
  $("stop").disabled = true;
  const r = await send({ type: "stopSession", sessionId: id, tabId });
  $("stop").disabled = false;
  if (r && !r.ok) { fail(r.error || "중단하지 못했습니다"); return; }
  fail("");
  // 화면에서도 내립니다. 받아 적기가 끝났는데 자막만 떠 있으면 아직 도는
  // 것처럼 보입니다. 쌓인 것은 서버에 그대로 남아 다시 고를 수 있습니다.
  //
  // 여기서는 고르개도 비웁니다. 내리기와 달리 그 세션은 이제 받지 않으므로
  // 「다시 얹기」가 가리킬 것이 없습니다.
  await send({ type: "watch", tabId, value: "" });
  $("pick").value = "";
  await syncHideButton();
  $("start-hint").textContent = "중단했습니다. 쌓인 자막은 그대로 남아 있습니다.";
  $("pick").length = 1;
  await fillPicker();
  setTimeout(refreshState, 400);
});
/* ---------- 이 탭에서 새로 시작 ---------- */

async function startWith(type) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  $("start-box").classList.add("busy");
  fail("");
  $("start-hint").textContent = "시작하는 중…";
  const r = await send({
    type, tabId: tab.id, url: tab.url,
    // 탭 제목이 곧 세션 이름입니다. 확장은 크롬이 감추는 그 제목을
    // 그냥 읽을 수 있습니다 -- 페이지 쪽에서는 트랙 label 이 불투명한
    // 식별자라 「탭 오디오」로만 남았습니다.
    title: (tab.title || "").replace(/\s+-\s+YouTube$/, ""),
    lang: start.lang || null, genre: start.genre || "general",
    refine: !!start.refine, profile: start.profile || "broadcast",
    viewerLang: "ko",
  });
  $("start-box").classList.remove("busy");
  if (!r || !r.ok) {
    fail((r && r.error) || "시작하지 못했습니다");
    $("start-hint").textContent = "";
    return;
  }
  $("start-hint").textContent = r.reloaded
    ? "받는 중입니다. 페이지를 새로고침해 얹었습니다."
    : r.skippedReload
      // 탭 소리를 잡는 중이라 새로고침하지 않았습니다. 그러면 자막이
      // 화면에 붙지 않으므로, 무엇을 해야 하는지 적어 둡니다.
      ? "받는 중입니다. 화면에 얹으려면 이 탭을 새로고침하십시오."
      : "받는 중입니다.";
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

$("start-url").addEventListener("click", () => startWith("startUrl"));
$("start-tab").addEventListener("click", () => startWith("startCapture"));

$("base").addEventListener("change", async (e) => {
  await send({ type: "setBase", base: e.target.value.replace(/\/+$/, "") });
  $("pick").length = 1;
  await fillPicker();
});

init();
