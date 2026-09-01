/* mimiwatch 화면 — 판올림: 위쪽 알림 띠와 「엔진 관리 › 판올림」 구역.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다.
 *
 * 서버(update.py)가 하루 한 번 깃허브 릴리스를 확인하고, 새 판이 보이면 bus 로
 * 알립니다. 여기서는 그 상태를 그리고 받기·적용 단추를 잇습니다. 「나중에」는
 * 그 태그에 한해 조용히 합니다 -- 다음 판이 나오면 다시 뜹니다. */

let updateInfo = null;

async function loadUpdateStatus() {
  try { renderUpdate(await (await fetch("/api/update")).json()); }
  catch (_) { /* 서버가 이 끝점을 모르는 예전 판. 띠는 서지 않습니다. */ }
}

/* bus.js 가 부릅니다. 서버가 보내는 알림은 status() 와 같은 모양입니다. */
function onUpdateEvent(m) { renderUpdate(m); }

const updateDismissKey = (st) =>
  "mimiwatch.updateDismissed." + ((st.latest && st.latest.tag) || "");

function renderUpdate(st) {
  updateInfo = st;
  const box = $("update-notice");
  if (!box) return;
  const tag = (st.latest && st.latest.tag) || "";

  // 엔진 관리의 구역은 언제나 지금 상태를 말합니다. 띠와 달리 닫히지 않습니다.
  const hint = $("update-hint");
  if (hint) {
    let s = `지금 ${st.current || "?"}`;
    if (tag) s += ` · 최신 ${tag}`;
    if (st.error) s += ` · ${st.error}`;
    else if (tag && !st.available) s += " · 최신입니다";
    else if (st.available) {
      s += st.frozen ? " · 새 판이 있습니다" : " · 새 판이 있습니다 (저장소에서는 git pull)";
    }
    hint.textContent = s;
  }

  const text = $("update-text");
  const dl = $("update-download"), ap = $("update-apply"), later = $("update-dismiss");
  dl.hidden = ap.hidden = later.hidden = true;
  let dismissed = false;
  try { dismissed = !!localStorage.getItem(updateDismissKey(st)); } catch (_) { /* 사설 창 */ }
  const busy = ["downloading", "ready", "applying"].includes(st.state);
  if ((!st.available && !busy) || (dismissed && !busy)) { box.hidden = true; return; }

  if (st.state === "downloading") {
    const p = st.progress || {};
    const pct = p.total ? ` ${Math.round(p.done / p.total * 100)}%` : "";
    text.textContent = `새 판 ${tag} 받는 중…${pct}`;
  } else if (st.state === "ready") {
    text.textContent = `새 판 ${tag}을 받아 두었습니다. 다시 시작하면 적용됩니다.`;
    ap.hidden = false;
  } else if (st.state === "applying") {
    text.textContent = "새 판으로 다시 시작하는 중입니다… 켜지면 이 화면이 다시 열립니다.";
  } else if (st.state === "error" && st.error) {
    text.textContent = `새 판 받기 실패: ${st.error}`;
    dl.hidden = !(st.frozen && st.latest && st.latest.asset);   // 다시 누르면 이어 받습니다
    later.hidden = false;
  } else if (st.frozen && st.latest && st.latest.asset) {
    text.textContent = `새 판 ${tag}이 나왔습니다.`;
    dl.hidden = false;
    later.hidden = false;
  } else {
    // 저장소에서 돌고 있거나(묶음이 아님) 이 플랫폼의 자산이 없는 릴리스입니다.
    text.textContent = `새 판 ${tag}이 나왔습니다 — 저장소에서는 git pull 로 받으십시오.`;
    later.hidden = false;
  }
  box.hidden = false;
}

async function updatePost(path) {
  const st = await (await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  })).json();
  if (st.error) alert(st.error);
  else renderUpdate(st);
  return st;
}

async function applyUpdate() {
  if (!confirm("새 판으로 갈아 끼우고 다시 시작할까요?\n받는 중인 방송은 먼저 제대로 닫힙니다.")) return;
  const res = await (await fetch("/api/update/apply", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  })).json();
  if (res.error) { alert(res.error); return; }
  renderUpdate({ ...(updateInfo || {}), state: "applying", available: true });
  // 서버가 꺼졌다가 새 판으로 돌아옵니다. 다시 응답하면 이 화면을 새로 엽니다.
  const until = Date.now() + 180000;
  const timer = setInterval(async () => {
    if (Date.now() > until) { clearInterval(timer); return; }
    try {
      await fetch("/api/update", { cache: "no-store" });
      clearInterval(timer);
      location.reload();
    } catch (_) { /* 아직 꺼져 있습니다 */ }
  }, 2000);
}

/* 이 파일은 main.js 보다 먼저 읽히지만 문서 끝이라 요소는 이미 있습니다.
 * bind() 를 건드리지 않고 여기서 잇습니다 -- 판올림의 요소는 전부 이 파일의
 * 것입니다. */
(function bindUpdate() {
  const on = (id, fn) => { const el = $(id); if (el) el.addEventListener("click", fn); };
  on("update-download", () => updatePost("/api/update/download"));
  on("update-apply", applyUpdate);
  on("update-dismiss", () => {
    try { if (updateInfo) localStorage.setItem(updateDismissKey(updateInfo), "1"); } catch (_) { /* 무해 */ }
    $("update-notice").hidden = true;
  });
  on("update-check", async () => {
    const hint = $("update-hint");
    if (hint) hint.textContent = "확인하는 중…";
    await updatePost("/api/update/check");
  });
  loadUpdateStatus();
})();
