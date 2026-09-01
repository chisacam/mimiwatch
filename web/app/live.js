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
/* 멈춘 세션의 안내 띠. 왜 멈췄는지에 따라 문구가 다르고, 할 수 있는 일을 단추로
 * 붙입니다 -- 「이어받기」와, 영상 id가 있으면 「전체 영상 전사」.
 *
 * 사용자가 스스로 「중단」한 세션(`stopped`)도 이어받을 수 있습니다. 예전에는
 * 되묻는 것이 성가시다고 권하지 않았는데, 라이브는 사용자가 잠깐 멈추고 돌아오는
 * 일이 잦고 그때 이어 붙일 길이 없으면 자막이 두 세션으로 갈립니다. 방송이 이미
 * 끝난 것(`stopped_by: ended`)만 예외입니다 -- 그때는 이어받을 것이 없고, 대신
 * 녹화본으로 남은 전체 영상을 전사할 수 있습니다. */
function offerResume(sessionId, why, st) {
  const box = $("live-notice");
  box.textContent = {
    tab: "자막 수신이 멈춰 있습니다. 탭을 다시 공유하면 이어서 쌓입니다 — ",
    error: "오류로 멈췄습니다. 원인이 사라졌으면 이어서 받을 수 있습니다 — ",
    interrupted: "서버가 멈춰 수신이 끊겼습니다 — ",
    stopped: "수신을 멈춘 방송입니다. 아직 진행 중이면 이어서 받을 수 있습니다 — ",
    ended: "끝난 방송입니다. 남은 녹화본을 통째로 전사할 수 있습니다 — ",
  }[why] || "수신이 멈춰 있습니다 — ";
  if (why !== "ended") {
    const b = document.createElement("button");
    b.className = "seg";
    b.textContent = "이어받기";
    b.onclick = () => resumeSession(sessionId, why, b);
    box.appendChild(b);
  }
  const vid = st && st.video_id;
  if (vid) {
    const r = document.createElement("button");
    r.className = "seg";
    r.textContent = "⟳ 전체 영상 전사";
    r.title = "방송이 끝나 녹화본으로 남았으면, 그 영상 전체를 다시 전사합니다";
    r.onclick = () => openRetranscribe(`https://www.youtube.com/watch?v=${vid}`,
                                       st.source_lang || "", st.title || "");
    box.appendChild(r);
  }
  box.hidden = false;
}

/* 끊긴 세션을 **같은 세션으로** 이어 붙입니다. 안내 띠의 단추와 목록 줄의 ▶ 가
 * 둘 다 여기로 옵니다. `btn` 은 누른 단추(있으면 진행을 적습니다). */
async function resumeSession(sessionId, why, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "이어받는 중…"; }
  // 대본 창을 여기서 잡습니다. 이 클릭이 살아 있는 유일한 지점입니다 --
  // 아래 공유 창을 고르고 나면 크롬이 window.open 을 막습니다. 시작할
  // 때와 같은 이유이고 같은 순서입니다.
  const pending = why === "tab" ? openPendingScriptWindow() : null;
  if (pending) state.scriptWin = pending;

  // 지금 선택기가 가리키는 엔진으로 이어받습니다. 멈춘 사이에 「관리」에서 바꾼 것이 있으면
  // 그것이 새 구간부터 쓰입니다.
  const res = await (await fetch("/api/live/resume", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: sessionId, asr: state.asr, backend: state.backend }),
  })).json();
  if (res.error) {
    if (pending) { pending.close(); state.scriptWin = null; }
    showLiveNotice(`이어받지 못했습니다 — ${res.error}`);
    if (btn) { btn.disabled = false; btn.textContent = "이어받기"; }
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
}

/* 왜 멈췄는지를 안내 띠의 문구 열쇠로. `resumeLive` 와 목록 줄이 같은 규칙을 씁니다. */
function stopReason(st) {
  if (st.source === "tab") return "tab";
  if (st.stopped_by === "ended") return "ended";
  if (st.state === "interrupted") return "interrupted";
  if (st.state === "error" || st.stopped_by === "stream") return "error";
  return "stopped";
}

/* 녹화본을 다시 전사합니다 -- 같은 엔진으로도 됩니다.
 *
 * 「영상 추가」 대화상자에 주소를 채워 엽니다. 거기서 전사 엔진·언어·장르를 고르고
 * 「시작」을 누르면 서버는 이미 있는 영상도 다시 전사합니다(글자가 같은 줄의
 * 번역과 손편집은 물려받습니다). 예전에는 다시 전사할 길이 이 대화상자에 같은
 * 주소를 다시 붙여 넣는 것뿐이라 그런 일이 되는지조차 알 수 없었습니다. */
function openRetranscribe(url, lang, title) {
  const f = $("add-form");
  renderAsrPicker();
  fillEngineSelect(f.querySelector('select[name="backend"]'), state.backends, state.backend, LOCKED.tr);
  f.source.value = "url";
  setAddSource("url");
  f.url.value = url;
  if ([...f.lang.options].some(o => o.value === (lang || ""))) f.lang.value = lang || "";
  const h = $("add-dialog").querySelector("h3");
  h.textContent = title ? `다시 전사 · ${title.slice(0, 40)}` : "다시 전사";
  $("add-dialog").showModal();
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
  // 멈춘 세션에서는 바꿀 것이 없습니다 -- 다음 이어받기가 새 엔진으로 시작합니다. 여기서
  // 안내 띠를 지우면 안 됩니다: 예전에는 엔진을 바꾸는 순간 「이어받기」 단추가 사라졌습니다.
  if (stale) return;
  if (!state.live || state.live.asr === state.asr) {
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
  const t = soloTile();
  bindLive(t, {
    id: res.id, store: MimiCues.create(), es: null, speakers: new Set(),
    url, lang, probe, asr: state.asr, backend: state.backend, source: res.source || "hls",
  }, {
    id: probe.id, title: probe.title, source_lang: lang || "",
    viewer_lang: $("viewer-lang").value, translated: false,
    backends_done: [state.backend], live: true,
  });
  t.src = srcOf({ site: probe.site, video_id: probe.id, channel: probe.channel, url });
  showTileInPanels(t);
  addLiveToPicker(probe, res.id);
  await attachLive(t);
}

/* 이미 있는 세션을 다시 엽니다 -- 탭을 새로고침했거나, 서버가 재시작되어
 * 수신은 끊겼지만 받아 적은 자막은 남아 있는 경우입니다. 자막은 서버가 SSE
 * 접속 직후에 그대로 되돌려 주므로, 여기서는 라이브를 새로 시작할 때와 같은
 * 그릇만 만들어 두면 나머지는 같은 이벤트 경로를 탑니다. 타일 하나로 봅니다 --
 * 멀티뷰의 한 칸으로 여는 것은 openSessionInTile 입니다. */
async function resumeLive(sessionId) {
  if (state.live && state.live.id === sessionId) return;   // 이미 보고 있음
  const st = await (await fetch(`/api/live/status/${sessionId}`)).json();
  if (!st.id) { jobError(st.error || "세션을 찾을 수 없습니다"); return; }
  const t = soloTile();
  detachTile(t);
  await openSessionInTile(t, st);
  showTileInPanels(t);
  await attachLive(t);
  // 탭 세션에는 끼워 넣을 영상이 없습니다. attachLive 가 앞서 본 것의
  // 안내를 지우고 지나가므로, 그 뒤에 이 흐름의 안내를 다시 씁니다.
  if (st.source === "tab") {
    const running = LIVE_RUNNING.includes(st.state);
    tabStageNotice(running ? "" : "수신은 멈춰 있고, 쌓인 자막 내역만 보고 있습니다.", t);
  }
}

/* 세션 하나를 타일에 매어 둡니다(아직 SSE 는 열지 않습니다 -- attachLive).
 * `st` 는 /api/live/status 의 답이거나 멀티뷰 멤버의 상태입니다. */
async function openSessionInTile(tile, st) {
  const running = LIVE_RUNNING.includes(st.state);
  bindLive(tile, {
    id: st.id, store: MimiCues.create(), es: null, speakers: new Set(),
    url: st.url, lang: st.source_lang || null, state: st.state,
    probe: { id: st.video_id, title: st.title },
    source: st.source || "hls",
    asr: st.asr_backend || "", backend: st.backend || "",
    lastStatus: running ? null : { ...st, type: "status" },
  }, {
    id: st.video_id || "", title: st.title || st.url,
    source_lang: st.source_lang || "", viewer_lang: st.viewer_lang,
    translated: false, backends_done: [st.backend], live: true,
  });
  tile.src = srcOf(st);
  tile.title = st.title || st.url || "";
  updateTileBar(tile);
}

/* 세션과 문서를 타일에 넣습니다. 초점 타일이면 화면의 전역(state.live 등)도 같이. */
function bindLive(tile, live, doc) {
  if (tile.live && tile.live.es && tile.live !== live) tile.live.es.close();
  tile.live = live;
  tile.doc = doc;
  tile.title = doc.title || "";
  if (tile === focusedTile()) {
    state.live = live;
    state.doc = doc;
    state.cues = live.store.cues;     // 저장소가 제자리에서 고치는 배열
    state.idx = -1;
  }
}

/* 초점 타일의 것을 오른쪽 자막 내역·위쪽 막대·조절기에 비춥니다. 시작할 때,
 * 이어 열 때, 초점이 옮겨 갈 때 -- 셋이 같은 꼬리를 지납니다. */
function showTileInPanels(tile) {
  const live = tile.live;
  state.live = live;
  state.doc = tile.doc;
  state.cues = live ? live.store.cues : ((tile.doc && tile.doc.cues) || []);
  state.idx = -1;
  if (live) {
    // 그때 쓰던 번역 백엔드로 맞춥니다. 저장된 번역문은 그 백엔드가 만든 것이라,
    // 지금 고른 백엔드 칸에 넣으면 하지 않은 일을 했다고 표시하게 됩니다.
    if (live.backend && state.backends.some(b => b.id === live.backend)) {
      state.backend = live.backend;
    }
    // 전사 엔진도 마찬가지입니다. 세션이 실제로 쓰는 것과 선택기가 가리키는
    // 것이 다르면, 다음에 무엇을 바꿔도 화면과 서버가 어긋난 채로 갑니다.
    if (live.asr && state.asrBackends.some(b => b.id === live.asr)) setAsr(live.asr);
  }
  buildScript();
  renderBackendPicker();
  applyModeForDoc();
  syncRenameButton();
  $("job").hidden = true;
  state.jobId = null;
  $("live-badge").hidden = !isLiveReceiving();
  // 탭 소리에는 맞출 영상이 없으므로 오프셋도 의미가 없습니다.
  $("offset-wrap").style.display = !live ? "" : (live.source === "tab" ? "none" : "flex");
  setNowTitle(tile.doc ? tile.doc.title : null);
  hideLiveNotice();
  if (live) renderLiveStatus(tile);
  else updateLangStatus();
  syncMvControls();
  pinScriptToBottom();
}

async function attachLive(tile) {
  const live = tile.live;
  // 영상 없는 세션(m3u8·탭 소리)은 아래에서 플레이어를 건너뛰므로, 앞서 본
  // 것이 남긴 안내 상자를 여기서 치웁니다.
  clearPlayerError(tile);
  const src = tile.src || { site: "none" };
  if (src.site !== "none") await mountTile(tile, src, { muted: tile !== focusedTile() });
  const es = new EventSource(`/api/live/events/${live.id}`);
  live.es = es;
  es.onmessage = (ev) => {
    if (tile.live !== live) return;          // 그 사이 타일이 다른 것을 보게 됐습니다
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "cue") onLiveCue(m, tile);
    else if (m.type === "translation") onLiveTranslation(m, tile);
    else if (m.type === "status") onLiveStatus(m, tile);
    // 서버가 4.5분마다 스트림을 일부러 닫습니다(확장의 서비스 워커 5분 규칙 때문).
    // 곧 이어질 onerror 는 끊김이 아니므로 「연결 끊김」을 띄우지 않습니다.
    else if (m.type === "rotate") live.rotating = true;
    // 다른 창에서 줄을 지웠습니다. 본 창과 대본 창이 같은 세션을 보고
    // 있으므로 한쪽에서 고친 것이 다른 쪽에도 닿아야 합니다.
    else if (m.type === "drop") dropCue(m.id, tile);
  };
  es.onerror = () => {
    // 끝난 세션은 서버가 백로그를 다 보내고 스트림을 닫습니다. 그것은 끊김이
    // 아니라 정상 종료이고, EventSource는 끊기면 알아서 다시 붙으므로 여기서
    // 닫지 않으면 몇 초마다 자막 전체를 다시 받게 됩니다. 진행 중인 세션은
    // 반대로 그 자동 재접속이 필요하니 그대로 둡니다.
    const st = live.state;
    if (st && !LIVE_RUNNING.includes(st)) { es.close(); return; }
    if (live.rotating) { live.rotating = false; return; }
    if (tile === focusedTile()) $("lang-status").innerHTML = "라이브 연결 끊김";
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
function markLiveStopped(sid) {
  const box = $("video-list");
  const row = sid ? box.querySelector(`.video-row[data-session="${CSS.escape(sid)}"]`)
                  : box.querySelector(".video-row.live.pending");
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

/* 자막 한 줄. 초점 타일이 아니면 저장소와 띠만 고칩니다 -- 화면의 자막 내역은
 * 초점 타일의 것이고, 초점이 오면 그 저장소로 다시 그립니다(showTileInPanels). */
function onLiveCue(m, tile = focusedTile()) {
  const live = tile && tile.live;
  if (!live) return;

  // 목록에 반영하는 규칙(같은 id는 갈아 끼우기, replaces는 빼기)은 확장과
  // 공유하는 cuestore.js 의 것입니다. 여기서는 빠진 줄의 행만 걷어 냅니다.
  const r = live.store.upsert(m);
  const cue = r.cue;
  const before = live.speakers.size;
  if (cue.speaker) live.speakers.add(cue.speaker);
  if (tile !== focusedTile()) {
    // 다른 창에서 써 넣은 줄은 시각이 마지막이 아닐 수 있습니다. 배열만 제자리로.
    if (r.isNew) resortCue(cue, live.store.cues);
    updateTileBar(tile);
    return;
  }
  r.removed.forEach(id => {
    const row = $("script").querySelector(`.line[data-id="${id}"]`);
    if (row) row.remove();
  });
  state.idx = -1;
  if (before < 2 && live.speakers.size >= 2) {
    // The chips just became meaningful; the lines already on screen need them.
    buildLiveScript();
  } else {
    appendScriptLine(cue);
    // 다른 창에서 써 넣은 줄(cue/add)은 도착 순서와 시각 순서가 다릅니다.
    if (r.isNew) resortCue(cue, live.store.cues);
  }
  renderCue();
  updateTileBar(tile);
}

function buildLiveScript() {
  $("script").textContent = "";
  state.cues.forEach(c => appendScriptLine(c));
}

function onLiveTranslation(m, tile = focusedTile()) {
  const live = tile && tile.live;
  if (!live) return;
  // 그 세션의 번역 백엔드 칸에 넣습니다. 초점이 오면 state.backend 도 그것으로 맞춰지므로
  // (showTileInPanels) 화면은 같은 칸을 읽습니다.
  const cue = live.store.translate(m.id, live.backend || state.backend, m.text);
  if (!cue) return;
  if (tile.doc && !tile.doc.translated) {
    tile.doc.translated = true;
    if (tile === focusedTile()) applyModeForDoc();
  }
  if (tile !== focusedTile()) return;
  const row = $("script").querySelector(`.line[data-id="${cue.id}"]`);
  if (row) refreshScriptRow(row, cue);
  // 번역 한 줄이 붙으면서 이 줄이 높아졌습니다. 바닥을 다시 잡습니다.
  pinScriptToBottom();
  renderCue();
}

/* 세션 상태가 왔습니다. 기억할 것(state·제목·엔진)은 어느 타일이든 적고, 부수 효과
 * (탭 캡처 멈춤·목록 표시)는 그 세션의 것만, 화면 글자는 초점 타일일 때만 고칩니다. */
function onLiveStatus(m, tile = focusedTile()) {
  const live = tile && tile.live;
  if (!live) return;
  live.state = m.state;
  live.lastStatus = m;
  if (m.asr_backend) live.asr = m.asr_backend;
  if (m.backend) live.backend = m.backend;
  // 이름이 바뀌면 따라갑니다. 「✎ 이름」으로 고치면 서버가 상태를 다시
  // 보내므로, 본 창에서 고친 것이 대본 창에도 같은 경로로 도착합니다.
  if (m.title && tile.doc && m.title !== tile.doc.title
      && !document.querySelector(".title-edit")) {
    tile.doc.title = m.title;
    tile.title = m.title;
    if (tile === focusedTile()) setNowTitle(m.title);
  }
  if (m.state === "error" || m.state === "stopped") {
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
    // 다룹니다 -- 왜 멈췼는지 적고, 받는 일만 멈춥니다. 탭 캡처는 **이 세션의
    // 것일 때만** 놓습니다 -- 멀티뷰의 다른 타일이 탭 소리를 올리는 중일 수 있습니다.
    if (state.captureSession === live.id) stopCapture();
    markLiveStopped(live.id);
  }
  updateTileBar(tile);
  if (tile === focusedTile()) renderLiveStatus(tile);
}

/* 초점 타일의 세션 상태를 위쪽 막대(#lang-status)·배지·안내 띠에 씁니다. 상태
 * 이벤트가 올 때와 초점이 이 타일로 올 때 -- 둘이 같은 글자를 써야 합니다. */
function renderLiveStatus(tile) {
  const live = tile.live;
  const el = $("lang-status");
  if (!live) return;
  $("live-badge").hidden = !isLiveReceiving();
  const m = live.lastStatus;
  if (!m) { el.className = "status"; el.textContent = ""; return; }
  if (m.state === "error") {
    el.className = "status warn";
    el.textContent = m.error || "라이브 오류";
    if (m.source === "tab" || m.url) offerResume(m.id, stopReason(m), m);
    return;
  }
  // 중단된 세션은 오류가 아닙니다. 수신은 끊겼지만 여기 떠 있는 자막은 진짜로
  // 받아 적은 것이므로, 세션을 접지 않고 왜 멈췄는지만 알립니다.
  if (m.state === "interrupted") {
    el.className = "status warn";
    el.innerHTML = `${LIVE_STATE.interrupted} · ${m.lines || 0}줄까지 남아 있습니다`;
    offerResume(m.id, stopReason(m), m);
    return;
  }
  if (m.state === "stopped") {
    // 다른 창이나 확장에서 「중단」했습니다. 수신만 멈춘 것이니 자막은 두고,
    // 이어받을 길을 띠에 둡니다. 멈춘 세션은 어떤 이유로 멈췼든 이어받을 수
    // 있습니다 -- 방송이 끝난 것만 이어받을 것이 없고 그때는 전체 영상 전사를 권합니다.
    if (m.source === "tab" || m.url) offerResume(m.id, stopReason(m), m);
  }
  el.className = "status";
  const src = m.source_lang || "auto";
  const eng = (m.asr || "").replace(/-Q8_0$|\.gguf$/g, "");
  el.innerHTML = `${LIVE_STATE[m.state] || m.state} · 원본 <b>${src}</b> → <b>${m.viewer_lang}</b>`
    + (eng ? ` · 전사 <b>${esc(eng)}</b>` : "")
    + (m.lines ? ` · ${m.lines}줄` : "")
    + (m.focused === false ? " · <b>대기</b>(소리만 받는 중)" : "");
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
  const t = tileBySession(live.id);
  if (t) detachTile(t);
  else if (live.es) live.es.close();
  state.live = null;
  hideLiveNotice();
  syncRenameButton();
  $("live-badge").hidden = true;
  $("offset-wrap").style.display = "";
}

function stopLive() {
  // 탭 공유는 세션보다 오래 살아남을 수 있습니다. 세션을 놓을 때 같이
  // 놓지 않으면 크롬의 "공유 중" 표시가 남고, 아무도 읽지 않는 오디오를
  // 계속 올립니다.
  const live = state.live;
  if (!live || state.captureSession === live.id) stopCapture();
  if (!live) return;
  if (live.es) live.es.close();
  hideLiveNotice();
  fetch("/api/live/stop", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: live.id }),
  }).catch(() => {});
  // 타일은 그대로 둡니다(방송은 계속 재생). 세션만 잊습니다.
  const t = tileBySession(live.id);
  if (t) { t.live = null; updateTileBar(t); }
  state.live = null;
  syncRenameButton();
  $("live-badge").hidden = true;
  $("offset-wrap").style.display = "";
  markLiveStopped(live.id);
  const el = $("lang-status");
  el.className = "status";
  el.textContent = "자막 중단됨 · 방송은 계속 재생됩니다";
}
