/* mimiwatch 화면 — 왼쪽 영상·방송 목록.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

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

/* 지난 방송을 지웁니다. 받는 중인 세션은 서버가 거절합니다 -- 먼저 「중단」.
 *
 * 예전에는 세션을 지울 길이 없어 목록이 자라기만 했습니다. 시험용 세션과
 * 실패한 세션이 쌓여 진짜 방송이 한도 밖으로 밀려났습니다. */
async function deleteSession(sid, title) {
  if (!confirm(`'${(title || "").slice(0, 50)}' 방송의 자막 내역을 삭제할까요?\n`
               + "받아 적은 자막이 함께 지워집니다.")) return;
  const res = await (await fetch("/api/live/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: sid }),
  })).json();
  if (res.error) { alert(res.error); return; }
  // 지운 것이 지금 보고 있는 것이면 화면을 비웁니다. 자막은 이제 없습니다.
  if (state.live && state.live.id === sid) {
    detachLive();
    state.doc = null; state.cues = []; state.idx = -1;
    buildScript();
    setNowTitle(null);
    if (overlay) overlay.clear();
  }
  await refreshVideoList();
}

/* 목록은 <select>가 아니라 행으로 그립니다.
 *
 * 고르기만 하던 때는 select로 충분했지만, 지우기와 상태 표시가 같은 자리에
 * 있어야 하고 제목도 한 줄로 잘리지 않아야 합니다. 삭제 단추가 헤더에
 * 따로 있으면 "지금 열려 있는 것"만 지울 수 있어, 목록에서 보이는 것과
 * 지워지는 것이 어긋납니다. */
/* 줄 오른쪽의 단추 묶음. 멈춘 방송에는 ▶ 이어받기와 ⟳ 전체 영상 전사, 녹화본에는
 * ⟳ 다시 전사, 끝난 것에는 🗑. 받는 중인 줄에는 아무것도 없습니다 -- 「중단」이
 * 먼저입니다. 처음 그릴 때와 상태가 바뀔 때(updateSessionRow) 같은 것을 씁니다. */
function rowActions({ value, session, title, stopped, deletable, videoId, st }) {
  const box = document.createElement("span");
  box.className = "vact";
  const add = (cls, text, tip, fn) => {
    const b = document.createElement("button");
    b.className = cls;
    b.title = tip;
    b.textContent = text;
    b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
    box.appendChild(b);
  };
  if (session && stopped) {
    const why = st ? stopReason(st) : "stopped";
    if (why !== "ended") {
      add("vres", "▶", "이어받기 — 같은 세션에 이어서 받습니다", async () => {
        openFromList(value);
        await resumeSession(session, why);
      });
    }
    if (videoId) {
      add("vre", "⟳", "전체 영상 전사 — 끝난 방송의 녹화본을 통째로 다시 전사합니다", () =>
        openRetranscribe(`https://www.youtube.com/watch?v=${videoId}`,
                         (st && st.source_lang) || "", title));
    }
  } else if (!session) {
    // 저장된 주소를 씁니다. 유튜브가 아닌 녹화본(m3u8 등)은 id 로 주소를 지을 수 없습니다.
    const url = (st && st.url) || `https://www.youtube.com/watch?v=${value}`;
    add("vre", "⟳", "다시 전사 — 같은 엔진으로도 됩니다. 글자가 같은 줄의 번역은 남습니다", () =>
      openRetranscribe(url, (st && st.source_lang) || "", title));
  }
  if (deletable) {
    add("vdel", "🗑", session ? "이 방송의 자막 내역을 삭제합니다" : "이 전사를 삭제합니다", () => {
      if (session) deleteSession(session, title);
      else deleteVideo(value, title);
    });
  }
  return box;
}

function videoRow({ value, session, title, meta, live, stopped, deletable, videoId, st }) {
  const row = document.createElement("div");
  row.className = "video-row" + (live ? " live" : "") + (stopped ? " stopped" : "");
  row.dataset.value = value;
  if (session) row.dataset.session = session;
  row.dataset.title = title;
  // 멀티뷰 묶음의 멤버는 ⊞ 표시를 달고, 누르면 묶음을 통째로 엽니다(openFromList).
  const group = (st && st.group) || "";
  row.dataset.group = group;
  if (group) row.classList.add("mv");

  // 제목만 있는 목록에서는 어느 방송인지 한눈에 오지 않습니다. 유튜브가
  // 주는 썸네일을 그대로 씁니다 -- 플레이어를 이미 임베드하고 있으므로
  // 브라우저는 어차피 구글과 통신합니다.
  const th = document.createElement("img");
  th.className = "vth";
  th.alt = "";
  th.decoding = "async";
  // 플레이어 임베드가 먼저입니다. 목록 그림 열넉 장이 같은 호스트로
  // 몰리면 그 뒤에 줄을 서게 됩니다.
  th.fetchPriority = "low";
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

  row.appendChild(rowActions({ value, session, title, stopped, deletable, videoId, st }));

  row.addEventListener("click", () => openFromList(value));
  // 플레이어 영역에 끌어다 놓으면 지금 보는 방송 옆에 타일로 붙습니다(tiles.js 의 dropRow).
  row.draggable = true;
  row.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/mimiwatch-row", value);
    e.dataTransfer.effectAllowed = "copy";
    setTimeout(() => $("player-wrap").classList.add("dragging"), 0);   // tiles.js 와 같은 이유
  });
  row.addEventListener("dragend", () => $("player-wrap").classList.remove("dragging"));
  return row;
}

/* 목록의 세션 줄 하나를 서버가 보낸 상태로 고칩니다. 줄 수, 상태, 이름.
 * 목록을 통째로 다시 그리지 않으므로 마우스를 올려 둔 줄이 흔들리지 않습니다. */
function updateSessionRow(row, s) {
  const running = LIVE_RUNNING.includes(s.state);
  const m = row.querySelector(".vm");
  if (m) {
    const n = s.lines != null ? s.lines : (s.cues || 0);
    m.textContent = `${n}줄` + (running ? "" : `  ·  ${LIVE_STATE[s.state] || s.state}`);
  }
  row.classList.toggle("stopped", !running);
  row.classList.remove("pending");
  const title = s.title || s.url || "";
  if (title && title !== row.dataset.title) {
    row.dataset.title = title;
    const t = row.querySelector(".vt");
    if (t) t.textContent = title;
    // 멀티뷰에서는 묶음의 줄이 전부 켜져 있으므로 「켜진 줄」이 아니라 「초점 세션」일 때만
    // 위쪽 제목을 바꿉니다.
    if (state.live && state.live.id === s.id && !document.querySelector(".title-edit")) setNowTitle(title);
  }
  // 단추 묶음은 상태에 따라 다릅니다. 받는 중이던 줄이 끝나면 ▶·⟳·🗑 이 생겨야
  // 하고, 이어받아 다시 받는 중이면 사라져야 합니다.
  row.lastElementChild.replaceWith(rowActions({
    value: "live:" + s.id, session: s.id, title, stopped: !running,
    deletable: !running, videoId: s.video_id || "", st: s,
  }));
}

function openFromList(value) {
  const row = $("video-list").querySelector(`.video-row[data-value="${CSS.escape(value)}"]`);
  const sid = row && row.dataset.session;
  markVideoRow(value);
  if (sid) {
    // 화면에 이미 타일로 있으면 초점만 옮깁니다. 묶음의 멤버면 묶음을 통째로 엽니다.
    const t = tileBySession(sid);
    if (t) { setFocus(t); return; }
    // 대본 창은 세션 하나만 읽습니다 -- 묶음을 통째로 열면 보이지 않는 타일마다 SSE 를 엽니다.
    if (row.dataset.group && !state.scriptOnly && (!state.mv || state.mv.id !== row.dataset.group)) {
      openMultiview(row.dataset.group).then(ok => { if (!ok) resumeLive(sid); });
      return;
    }
    resumeLive(sid);
    return;
  }
  if (state.live) {
    // 녹화본을 열어도 라이브 수신은 끊지 않습니다. 사용자가 「중단」을 누른
    // 것이 아니고, 목록의 녹화본은 이미 전사·번역이 끝난 것이라 방송 쪽에
    // 부담이 되지 않습니다. 화면만 떼고(detach) 서버는 계속 받습니다 --
    // 목록의 그 방송 줄을 다시 누르면 이어서 봅니다.
    //
    // 탭 소리만 예외입니다. 그 스트림은 이 창에 매여 있어 다른 것을 여는
    // 순간 어차피 끊기므로, 서버에도 끝났다고 말해 줍니다.
    if (state.live.source === "tab") stopLive();
    else detachLive();
  } else {
    // 끝난 방송의 임시 줄은 다른 것을 열면 치웁니다.
    dropLiveOption(value);
  }
  loadVideo(value);
}

function markVideoRow(value) {
  // 멀티뷰면 묶음의 멤버 줄이 전부 켜집니다 -- 화면에 다 떠 있으니까요.
  const gid = state.mv && state.mv.id;
  $("video-list").querySelectorAll(".video-row").forEach(r =>
    r.classList.toggle("on", r.dataset.value === value || (!!gid && r.dataset.group === gid)));
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

async function refreshVideoList(selectId, pre) {
  // 시작할 때는 이미 받아 둔 것을 넘겨받습니다. 예전에는 init 이 두 요청을
  // 보내고 여기서 같은 둘을 또 보냈습니다.
  const [list, sessions] = pre || await Promise.all([
    fetch("/api/videos").then(r => r.json()),
    fetch("/api/live/sessions").then(r => r.json()),
  ]);
  const box = $("video-list");
  // 멀티뷰에서는 묶음의 줄이 전부 켜져 있어 「첫 켜진 줄」이 초점이 아닐 수 있습니다.
  // 지금 보는 것(초점 세션)을 기준으로 잡습니다.
  const current = box.querySelector(".video-row.on");
  const keep = state.live ? "live:" + state.live.id : (current && current.dataset.value);
  box.textContent = "";

  // 라이브 세션에는 큐 파일이 없어서, 예전에는 탭을 닫으면 그 방송의 자막이
  // 통째로 사라졌습니다. 이제 서버가 들고 있으므로 목록에 올려 다시 엽니다.
  // 한 줄도 못 받은 세션은 열어 봐야 볼 것이 없으니 뺍니다.
  // 한 줄도 못 받은 세션은 열어 봐야 볼 것이 없으니 뺍니다 -- 받는 중인 것은
  // 예외입니다. 다른 창에서 막 시작한 방송이 첫 자막 전에도 보여야 합니다.
  sessions.filter(s => s.cues || LIVE_RUNNING.includes(s.state)).forEach(s => {
    const running = LIVE_RUNNING.includes(s.state);
    box.appendChild(videoRow({
      value: "live:" + s.id, session: s.id,
      title: s.title || s.url, videoId: s.video_id || "",
      meta: `${s.cues}줄` + (running ? "" : `  ·  ${LIVE_STATE[s.state] || s.state}`),
      // 끝난 방송만 지울 수 있습니다. 받는 중인 것은 「중단」이 먼저입니다.
      live: true, stopped: !running, deletable: !running, st: s,
    }));
  });
  list.forEach(v => {
    // 로컬 파일은 probe 때 길이를 모릅니다(ffprobe 는 준비물이 아님). 전사가 잰 것을 씁니다.
    const secs = v.duration || v.audio_seconds;
    const mins = secs ? `${Math.round(secs / 60)}분` : "";
    box.appendChild(videoRow({
      value: v.id, title: v.title, videoId: v.source === "file" ? "" : v.id,
      meta: [v.source_lang + (v.translated ? `→${v.viewer_lang}` : ""), mins]
        .filter(Boolean).join("  ·  "),
      deletable: true, st: v,
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
