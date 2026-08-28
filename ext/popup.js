/* 확장 팝업. 지금 탭에 무엇을 얹을지 고르고, 자막 모양을 조절합니다.
 *
 * 세션을 시작하고 관리하는 일은 mimiwatch 페이지가 합니다. 여기서 그것까지
 * 하면 목록·추가·엔진 설정 UI를 팝업에 다시 지어야 하고, 두 벌이 됩니다.
 * 이 팝업이 답하는 것은 「이 유튜브 탭에 어느 자막을 얹을까」 하나입니다.
 */
const $ = (id) => document.getElementById(id);

const send = (msg) => new Promise((r) => chrome.runtime.sendMessage(msg, r));
const toTab = (tabId, msg) =>
  new Promise((r) => chrome.tabs.sendMessage(tabId, msg, () => r(chrome.runtime.lastError ? null : true)));

let tabId = null;
let prefs = { mode: "both", showPrev: true, size: 30, dim: 0.55, offset: 0 };

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
  syncModes();

  await fillPicker();
  await refreshState();
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
    $("state").textContent = r.mounted
      ? `자막 ${r.cues}줄` + (r.live ? (r.receiving ? " · 받는 중" : " · 종료된 방송") : " · 녹화본")
      : "고르면 이 탭에 얹습니다.";
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
  if (r && !r.ok) fail(r.error || "붙이지 못했습니다");
  setTimeout(refreshState, 600);
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
$("reset-pos").addEventListener("click", () => { prefs.pos = null; pushPrefs(); });
$("stop").addEventListener("click", async () => {
  await send({ type: "watch", tabId, value: "" });
  $("pick").value = "";
  setTimeout(refreshState, 400);
});
$("base").addEventListener("change", async (e) => {
  await send({ type: "setBase", base: e.target.value.replace(/\/+$/, "") });
  $("pick").length = 1;
  await fillPicker();
});

init();
