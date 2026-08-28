/* mimiwatch 화면 — 라이브 세션: 시작·이어받기·SSE 수신·중단.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

function hideLiveNotice() {
  const box = $("live-notice");
  box.hidden = true;
  box.textContent = "";
}

/* 끊긴 세션을 같은 세션으로 이어 붙입니다. 자막은 세션 id로 저장되므로
 * 그때까지의 스크립트가 그대로 남고 뒤에 이어 붙습니다. */
/* `why` 는 왜 멈췄는지입니다. 서버가 죽은 것과 공유가 끝난 것은 다른 일이고,
 * 다시 시작하려면 해야 할 일도 다릅니다 -- 탭 소리는 브라우저가 다시
 * 들려줘야 합니다. */
function offerResume(sessionId, why) {
  const box = $("live-notice");
  box.textContent = {
    tab: "자막 수신이 멈춰 있습니다. 탭을 다시 공유하면 이어서 쌓입니다 — ",
    error: "오류로 멈췄습니다. 원인이 사라졌으면 이어서 받을 수 있습니다 — ",
  }[why] || "서버가 멈춰 수신이 끊겼습니다 — ";
  const b = document.createElement("button");
  b.className = "seg";
  b.textContent = "이어받기";
  b.onclick = async () => {
    b.disabled = true;
    b.textContent = "이어받는 중…";
    // 대본 창을 여기서 잡습니다. 이 클릭이 살아 있는 유일한 지점입니다 --
    // 아래 공유 창을 고르고 나면 크롬이 window.open 을 막습니다. 시작할
    // 때와 같은 이유이고 같은 순서입니다.
    const pending = why === "tab" ? openPendingScriptWindow() : null;
    if (pending) state.scriptWin = pending;

    const res = await (await fetch("/api/live/resume", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: sessionId }),
    })).json();
    if (res.error) {
      if (pending) { pending.close(); state.scriptWin = null; }
      showLiveNotice(`이어받지 못했습니다 — ${res.error}`);
      return;
    }
    hideLiveNotice();
    // 같은 세션이므로 다시 붙기만 하면 됩니다. detachLive 로 지금 붙어
    // 있는 것을 끊고 새로 열어야 상태 이벤트를 처음부터 받습니다.
    detachLive();
    state.live = null;
    await resumeLive(sessionId);
    await refreshVideoList();
    // 탭 소리는 서버가 되감을 수 없습니다. 브라우저가 다시 들려줘야
    // 이어집니다 -- 세션만 살아나고 소리가 오지 않으면 「받는 중」인 채로
    // 한 줄도 늘지 않습니다.
    if (res.source !== "tab") return;
    const media = await requestTabAudio();
    if (!media) {
      if (pending) { pending.close(); state.scriptWin = null; }
      // 세션은 이미 살아났는데 소리가 오지 않습니다. 그 상태를 화면에
      // 적어 두지 않으면 「받는 중」인 채로 한 줄도 늘지 않는 이유를
      // 알 길이 없습니다.
      tabStageNotice("탭을 다시 공유해야 이어집니다.");
      showLiveNotice("탭을 다시 공유해야 이어집니다. 목록에서 다시 "
                     + "「이어받기」를 누르십시오.");
      return;
    }
    await pipeCapture(media, sessionId);
    tabStageNotice();
    aimScriptWindow(pending, sessionId);
  };
  box.appendChild(b);
  box.hidden = false;
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
  syncRenameButton();
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
                 source: st.source || "hls",
                 asr: st.asr_backend || "" };
  buildScript();
  renderBackendPicker();
  applyModeForDoc();
  syncRenameButton();
  $("job").hidden = true;
  state.jobId = null;
  $("live-badge").hidden = !running;
  // 탭 소리에는 맞출 영상이 없으므로 오프셋도 의미가 없습니다.
  $("offset-wrap").style.display = st.source === "tab" ? "none" : "flex";
  // 멈춘 세션은 이어받을 수 있습니다. 자동으로 하지 않습니다 -- 다시 받기
  // 시작하는 것은 눌러서 시킬 일입니다.
  //
  // 주소로 받는 세션은 `interrupted`(서버가 죽음)일 때만 권합니다.
  // `stopped` 는 사용자가 「중단」을 누른 것이라 다시 묻는 것이 성가십니다.
  //
  // 탭 소리는 다릅니다. 공유를 멈추거나 그 탭을 닫으면 `stopped` 로
  // 끝나는데, 그것이 정상 종료 경로입니다 -- 서버를 정상으로 내려도
  // 마찬가지입니다. 그러니 멈춰 있으면 언제나 권합니다. 같은 세션으로
  // 이어야 스크립트가 한 줄기로 남습니다.
  if (!running) {
    if (st.source === "tab") offerResume(st.id, "tab");
    // 오류로 끝난 세션도 이어받을 수 있어야 합니다. 모델 파일을 못 찾았다든가
    // 하는 이유는 대개 고치고 나면 사라지는 것이고, 그때 이어붙일 자리가
    // 없으면 받아 둔 자막을 버리고 새로 시작하는 수밖에 없습니다.
    //
    // `stopped`여도 서버가 「수신이 끊겨서」(stopped_by=stream)라고 적어 두었으면
    // 권합니다. 사용자가 「중단」한 것(user)과 방송이 끝난 것(ended)은 아닙니다.
    else if (st.url && (st.state === "interrupted" || st.state === "error"
                        || (st.state === "stopped" && st.stopped_by === "stream"))) {
      offerResume(st.id, st.state === "stopped" ? "error" : st.state);
    }
  }
  await attachLive(st.id, st.video_id);
  // 탭 세션에는 끼워 넣을 영상이 없습니다. attachLive 가 앞서 본 것의
  // 안내를 지우고 지나가므로, 그 뒤에 이 흐름의 안내를 다시 씁니다.
  if (st.source === "tab") {
    tabStageNotice(running ? "" : "수신은 멈춰 있고, 쌓인 자막 내역만 보고 있습니다.");
  }
}

async function attachLive(sessionId, videoId) {
  // 영상 없는 세션(m3u8·탭 소리)은 아래에서 플레이어를 건너뛰므로, 앞서 본
  // 것이 남긴 안내 상자를 여기서 치웁니다. createPlayer 만 믿으면 그 경로가
  // 안 지나가서 옛 안내문이 그대로 남습니다.
  clearPlayerError();
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
    // 다른 창에서 줄을 지웠습니다. 본 창과 대본 창이 같은 세션을 보고
    // 있으므로 한쪽에서 고친 것이 다른 쪽에도 닿아야 합니다.
    else if (m.type === "drop") dropCue(m.id);
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
  // 이름이 바뀌면 따라갑니다. 「✎ 이름」으로 고치면 서버가 상태를 다시
  // 보내므로, 본 창에서 고친 것이 대본 창에도 같은 경로로 도착합니다.
  if (m.title && state.doc && m.title !== state.doc.title
      && !document.querySelector(".title-edit")) {
    state.doc.title = m.title;
    setNowTitle(m.title);
  }
  if (m.state === "error") {
    el.className = "status warn";
    el.textContent = m.error || "라이브 오류";
    // 여기서 stopLive() 를 부르고 있었습니다. 그것이 state.live 를 비우는
    // 바람에 두 가지가 무너졌습니다.
    //
    //   - SSE 는 상태를 **먼저** 보내고 쌓인 자막을 뒤에 보냅니다. 그래서
    //     이 줄을 지날 때 state.live 가 비면, 곧이어 도착하는 백로그가
    //     onLiveCue 의 첫 줄(`if (!live) return`)에서 전부 버려집니다.
    //     312줄을 받아 둔 세션을 열어도 오류 한 줄만 보였습니다.
    //   - hideLiveNotice() 가 방금 띄운 「이어받기」를 지웠습니다.
    //
    // 오류는 세션을 잊을 이유가 아닙니다. 받아 적어 둔 것은 진짜이고,
    // 오류야말로 다시 시도하고 싶은 자리입니다. 그래서 「중단됨」과 같게
    // 다룹니다 -- 왜 멈췄는지 적고, 받는 일만 멈춥니다.
    stopCapture();
    $("live-badge").hidden = true;
    markLiveStopped();
    if (state.live) state.live.state = "error";
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
  // 탭 공유는 세션보다 오래 살아남을 수 있습니다. 세션을 놓을 때 같이
  // 놓지 않으면 크롬의 "공유 중" 표시가 남고, 아무도 읽지 않는 오디오를
  // 계속 올립니다.
  stopCapture();
  const live = state.live;
  if (!live) return;
  if (live.es) live.es.close();
  hideLiveNotice();
  fetch("/api/live/stop", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: live.id }),
  }).catch(() => {});
  state.live = null;
  syncRenameButton();
  $("live-badge").hidden = true;
  $("offset-wrap").style.display = "";
  markLiveStopped();
  const el = $("lang-status");
  el.className = "status";
  el.textContent = "자막 중단됨 · 방송은 계속 재생됩니다";
}
