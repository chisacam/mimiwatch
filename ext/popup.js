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
  await fillGenres();
  await refreshState();
  // 유튜브 탭이 아니면 시작할 것도 없습니다.
  $("start-box").classList.toggle("busy", !onYouTube);
}

/* 장르는 서버가 들고 있습니다. 팝업에 붙박이로 적어 두면 서버에서 늘릴
 * 때마다 어긋납니다. */
async function fillGenres() {
  const r = await send({ type: "backends" });
  if (!r || !r.ok) return;
  const sel = $("genre");
  for (const g of (r.data.genres || [])) {
    sel.append(new Option(g.label || g.id, g.id));
  }
  if (!sel.length) sel.append(new Option("일반", "general"));
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
  // 화면에서 치우기만 합니다. 세션은 서버에서 계속 돕니다 -- 받아 적던 것을
  // 끝내려면 mimiwatch 페이지의 「중단」입니다.
  await send({ type: "watch", tabId, value: "" });
  $("pick").value = "";
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
    lang: $("lang").value || null, genre: $("genre").value || "general",
    viewerLang: "ko",
  });
  $("start-box").classList.remove("busy");
  if (!r || !r.ok) {
    fail((r && r.error) || "시작하지 못했습니다");
    $("start-hint").textContent = "";
    return;
  }
  $("start-hint").textContent = "받는 중입니다.";
  $("pick").length = 1;
  await fillPicker();
  $("pick").value = "live:" + r.id;
  setTimeout(refreshState, 800);
}

$("start-url").addEventListener("click", () => startWith("startUrl"));
$("start-tab").addEventListener("click", () => startWith("startCapture"));

$("base").addEventListener("change", async (e) => {
  await send({ type: "setBase", base: e.target.value.replace(/\/+$/, "") });
  $("pick").length = 1;
  await fillPicker();
});

init();
