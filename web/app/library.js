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

  if (deletable) {
    const del = document.createElement("button");
    del.className = "vdel";
    del.title = session ? "이 방송의 자막 내역을 삭제합니다" : "이 전사를 삭제합니다";
    del.textContent = "🗑";
    del.addEventListener("click", (e) => {
      e.stopPropagation();          // 지우려다 열리면 안 됩니다
      if (session) deleteSession(session, title);
      else deleteVideo(value, title);
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

async function refreshVideoList(selectId, pre) {
  // 시작할 때는 이미 받아 둔 것을 넘겨받습니다. 예전에는 init 이 두 요청을
  // 보내고 여기서 같은 둘을 또 보냈습니다.
  const [list, sessions] = pre || await Promise.all([
    fetch("/api/videos").then(r => r.json()),
    fetch("/api/live/sessions").then(r => r.json()),
  ]);
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
      // 끝난 방송만 지울 수 있습니다. 받는 중인 것은 「중단」이 먼저입니다.
      live: true, stopped: !running, deletable: !running,
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
