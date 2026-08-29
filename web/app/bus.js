/* mimiwatch 화면 — 서버가 밀어 주는 전역 변화(세션·영상·작업)를 받아 그 부분만 갱신합니다.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다.
 *
 * 라이브 자막은 세션마다 SSE 로 오지만 그 밖의 것 -- 새 세션, 세션의 줄 수와
 * 상태, 전사가 끝나 늘어난 영상, 다른 창에서 돌고 있는 작업 -- 은 새로고침해야
 * 보였습니다. 확장에서 시작한 방송이 이 화면에 나타나지 않았고, 목록의
 * 「받는 중」은 방송이 끝난 뒤에도 그대로였습니다. 서버의 `/api/events` 한
 * 줄기를 붙여 두고, 알림의 종류에 따라 목록의 한 줄만 고치거나 목록을 다시
 * 읽습니다. 끊기면 EventSource 가 스스로 다시 붙고, 그 사이 놓친 것은 다시
 * 붙은 뒤 목록을 한 번 새로 읽어 메웁니다. */

let bus = null;
let busWasDown = false;
let busRotating = false;
let listTimer = null;
let listWaiters = [];

/* 목록 다시 읽기를 잠깐 모아서 합니다. 알림은 자막 한 줄마다 올 수 있는데
 * 그때마다 요청 둘을 보내고 목록을 다시 그릴 이유는 없습니다.
 *
 * 다시 읽기가 **끝난 뒤**에 이어서 할 일은 돌려주는 약속에 걸어야 합니다.
 * 따로 시계를 두면 어긋납니다 -- 뒤에 있는 탭은 크롬이 타이머를 늦추므로
 * 「700ms 뒤에 그 줄을 고르기」가 「400ms 뒤 목록 읽기」보다 먼저 돌아
 * 없는 줄을 고르는 일이 실제로 있었습니다. */
function scheduleListRefresh(delay = 400) {
  if (state.scriptOnly) return Promise.resolve();
  return new Promise((resolve) => {
    listWaiters.push(resolve);
    clearTimeout(listTimer);
    listTimer = setTimeout(async () => {
      listTimer = null;
      const waiters = listWaiters;
      listWaiters = [];
      try { await refreshVideoList(); } catch (_) { /* 다음 알림에 다시 */ }
      waiters.forEach(w => w());
    }, delay);
  });
}

function connectBus() {
  if (bus || state.scriptOnly) return;
  bus = new EventSource("/api/events");
  bus.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "session") onSessionChanged(m);
    else if (m.type === "video") onVideoChanged(m);
    else if (m.type === "job") onJobChanged(m);
    // 서버가 주기적으로 닫는 것(rotate)은 끊김이 아닙니다. 다시 붙어도 목록을 새로 읽지 않습니다.
    else if (m.type === "rotate") busRotating = true;
    // 모델 내려받기의 진행·완료·실패. 대화상자가 열려 있으면 그 줄을 고치고,
    // 필요한 것이 다 갖춰지면 위쪽 안내 띠를 내립니다.
    else if (m.type === "model") onModelEvent(m);
  };
  bus.onopen = () => {
    if (busWasDown) scheduleListRefresh(0);   // 끊긴 사이의 변화를 메웁니다
    busWasDown = false;
  };
  bus.onerror = () => {
    if (busRotating) { busRotating = false; return; }
    busWasDown = true;
  };
  // 뒤에 있던 탭은 크롬이 타이머를 1분에 한 번으로 늦추고, 오래 두면 통째로
  // 얼립니다(메모리 절약). 그 사이 알림이 밀리거나 끊길 수 있으므로, 탭이
  // 다시 보이면 목록을 한 번 새로 읽어 그동안의 변화를 메웁니다 -- 유튜브를
  // 다른 탭에서 보다가 돌아오는 것이 이 화면의 보통 쓰임입니다.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleListRefresh(0);
  });
}

/* 세션 하나가 바뀌었습니다. 목록에 있으면 그 줄만 고치고, 없으면(다른 창이나
 * 확장에서 막 시작한 것) 목록을 다시 읽습니다. 이 화면이 아무것도 보고 있지
 * 않으면 새로 시작된 방송을 그대로 엽니다 -- 처음 열 때 받는 중인 방송으로
 * 돌아가는 것과 같은 규칙입니다. */
function onSessionChanged(m) {
  const box = $("video-list");
  const row = box.querySelector(`.video-row[data-session="${CSS.escape(m.id)}"]`);
  if (m.deleted) {
    if (row) row.remove();
    if (state.live && state.live.id === m.id) {
      detachLive();
      state.doc = null; state.cues = []; state.idx = -1;
      buildScript(); setNowTitle(null);
      if (overlay) overlay.clear();
    }
    if (!box.querySelector(".video-row")) scheduleListRefresh(0);   // 빈 목록 안내
    return;
  }
  if (row) {
    updateSessionRow(row, m);
    return;
  }
  scheduleListRefresh().then(() => {
    if (state.doc || state.live || !LIVE_RUNNING.includes(m.state)) return;
    if (!box.querySelector(`.video-row[data-session="${CSS.escape(m.id)}"]`)) return;
    markVideoRow("live:" + m.id);
    resumeLive(m.id);
  });
}

/* 영상(녹화본)이 바뀌었습니다 -- 전사가 끝났거나, 번역이 붙었거나, 지워졌거나.
 * 목록은 다시 읽고, 그 영상을 열어 둔 화면은 자막을 다시 읽습니다. 다만
 * 읽기 모드일 때만 -- 편집기나 고르기가 열려 있는데 자막 내역을 통째로 다시
 * 그리면 하던 일이 날아갑니다. 이 창이 그 작업을 돌린 것이면(jobLocal) 그
 * 폴링 루프가 알아서 다시 읽으므로 여기서는 하지 않습니다. */
function onVideoChanged(m) {
  scheduleListRefresh();
  if (!state.doc || isLiveDoc() || state.doc.id !== m.id) return;
  if (m.reason === "deleted") {
    state.doc = null; state.cues = []; state.idx = -1;
    buildScript(); setNowTitle(null);
    if (overlay) overlay.clear();
    return;
  }
  if (state.scriptMode !== "read") return;
  if (state.jobId && state.jobLocal) return;
  reloadCues().catch(() => {});
}

/* 다른 창(또는 확장)에서 시작한 작업의 진행. 이 창의 것은 폴링 루프가 그리므로
 * 건너뜁니다. */
function onJobChanged(st) {
  if (state.jobId === st.id && state.jobLocal) return;
  renderForeignJob(st);
}
