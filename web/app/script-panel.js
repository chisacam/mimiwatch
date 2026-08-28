/* mimiwatch 화면 — 오른쪽 자막 내역: 줄 그리기, 편집기, 재번역 고르기, 따로 띄운 창.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

/* ---------- script panel ---------- */
function buildScript() {
  const box = $("script");
  box.textContent = "";
  state.cues.forEach((c, i) => box.appendChild(scriptRow(c, i)));
}

/* 줄 한 개를 만듭니다.
 *
 * 녹화본과 라이브가 각자 만들고 있었습니다. 몸통을 채우는 코드가 두 벌이라
 * 화자 표시는 한쪽에만 있었고, 편집을 붙이려면 또 두 벌이 될 뻔했습니다.
 * 다른 것은 무엇으로 줄을 찾느냐뿐입니다 -- 녹화본은 위치(data-i)로,
 * 라이브는 자막 번호(data-id)로. 둘 다 답니다. */
function scriptRow(c, i) {
  const row = document.createElement("div");
  row.className = "line";
  if (i != null) row.dataset.i = i;
  if (c.id != null) row.dataset.id = c.id;
  const t = document.createElement("div");
  t.className = "t";
  const body = document.createElement("div");
  row.append(t, body);
  refreshScriptRow(row, c);
  row.addEventListener("click", () => {
    // 편집 중인 줄에서는 눌러도 움직이지 않습니다. 글자를 고르려던 것이
    // 재생 위치를 옮겨 버리면 고칠 수가 없습니다.
    if (row.classList.contains("editing")) return;
    if (state.scriptMode === "edit") { openCueEditor(row, c); return; }
    // 고르는 중에는 움직이지 않습니다. 고르기는 pointerdown 에서 하는데,
    // 거기서 preventDefault() 를 해도 click 은 그대로 옵니다 -- 그것이 막는
    // 것은 글자 선택 같은 기본 동작이지 뒤따르는 click 이벤트가 아닙니다.
    if (state.scriptMode === "tr") return;
    if (state.player) { state.player.seekTo(cueStart(c), true); state.player.playVideo(); }
  });
  // 번역 모드의 고르기. click 이 아니라 pointerdown 에 거는 것은 shift+click 이
  // 글자 선택을 함께 일으키기 때문입니다 -- 그쪽을 먼저 막아야 합니다.
  row.addEventListener("pointerdown", (e) => {
    if (state.scriptMode !== "tr") return;
    e.preventDefault();
    pickRow(c.id, e.shiftKey);
  });
  return row;
}

function refreshScriptRow(row, c) {
  row.firstElementChild.textContent = fmt(cueStart(c));
  // 줄의 짜임은 [시각, 몸통, ✎] 입니다. lastElementChild 로 몸통을 잡으면
  // 두 번째 호출부터 단추를 지우게 됩니다.
  const body = row.children[1];
  body.textContent = "";
  const tx = document.createElement("div");
  tx.className = "tx";
  const chip1 = chipFor(c);
  if (chip1) {
    const chip = document.createElement("b");
    chip.className = "spk-chip";
    chip.textContent = chip1;
    tx.appendChild(chip);
  }
  tx.appendChild(document.createTextNode(c.text));
  body.appendChild(tx);
  const trText = trOf(c);
  if (trText) {
    const tr = document.createElement("div");
    tr.className = "tr";
    tr.textContent = trText;
    // 원문을 고쳤으면 붙어 있는 번역은 **고치기 전 문장**의 번역입니다.
    // 지우지 않고 그렇게 표시만 합니다 -- 틀린 번역이라도 없는 것보다
    // 낫고, 다시 번역할지는 사람이 정할 일입니다.
    if (String(c.edited || "").includes("text")) {
      const warn = document.createElement("b");
      warn.className = "tr-stale";
      warn.title = "원문을 고친 뒤라 이 번역은 옛 문장의 것입니다";
      warn.textContent = "⟲ 원문과 다름";
      tr.appendChild(warn);
    }
    body.appendChild(tr);
  }
  row.classList.toggle("pending", c.kind === "final" && !trText);
  // 편집 단추. 줄에 얹어 두고 CSS 가 hover 일 때만 보입니다. 다시 그릴
  // 때마다 새로 답니다 -- 닫힌 값(c)을 물고 있어서 옛것을 남기면 고친
  // 내용이 아니라 고치기 전 값으로 편집기가 열립니다.
  row.querySelector(":scope > .line-edit")?.remove();
  const pen = document.createElement("button");
  pen.className = "line-edit";
  pen.title = "이 줄을 고칩니다";
  pen.textContent = "✎";
  pen.addEventListener("click", (e) => { e.stopPropagation(); openCueEditor(row, c); });
  row.appendChild(pen);
}

function appendScriptLine(c) {
  const box = $("script");
  const existing = box.querySelector(`.line[data-id="${c.id}"]`);
  if (existing) {
    // 정제본이 같은 id의 줄을 갈아 끼웁니다. 글자 수가 달라지므로 높이도
    // 달라집니다.
    refreshScriptRow(existing, c);
    pinScriptToBottom();
    return;
  }
  box.appendChild(scriptRow(c, null));
  pinScriptToBottom();
}

/* ---------- 자막 고치기 ----------
 *
 * 전사는 틀립니다. 잡음을 말로 듣고, 고유명사를 엉뚱하게 적고, 번역은 그
 * 위에서 한 번 더 어긋납니다. 내보내기까지 붙은 마당에 고칠 방법이 없으면
 * 틀린 채로 나갑니다.
 *
 * 고치는 것은 서버가 곧바로 저장합니다. 자막은 이제 녹화본이든 라이브든
 * 같은 표에 한 줄씩 들어 있어서, 한 줄을 고치는 데 그 영상 전체를 다시 쓸
 * 일이 없습니다. */
function openCueEditor(row, c) {
  if (row.classList.contains("editing")) return;
  const owner = editOwner();
  if (!owner) return;
  row.classList.add("editing");
  const body = row.children[1];
  const keep = body.cloneNode(true);

  const box = document.createElement("div");
  box.className = "cue-edit";
  const src = document.createElement("textarea");
  src.className = "ce-src";
  src.rows = 2;
  src.value = c.text || "";
  const tr = document.createElement("textarea");
  src.placeholder = "원문";
  tr.className = "ce-tr";
  tr.rows = 2;
  tr.placeholder = "번역 (비우면 그대로 둡니다)";
  tr.value = trOf(c) || "";
  const bar = document.createElement("div");
  bar.className = "ce-bar";
  const at = document.createElement("input");
  at.type = "number"; at.step = "0.1"; at.className = "ce-at";
  at.value = (Math.round(cueStart(c) * 10) / 10).toFixed(1);
  at.title = "이 줄이 뜨는 시각(초)";
  const save = mkbtn("저장", "primary-seg");
  const del = mkbtn("🗑", "danger");
  del.title = "이 줄을 지웁니다";
  const cancel = mkbtn("취소", "");
  bar.append(at, document.createElement("span"), save, del, cancel);
  bar.children[1].className = "grow";
  box.append(src, tr, bar);
  body.textContent = "";
  body.appendChild(box);
  src.focus();

  const close = () => {
    row.classList.remove("editing");
    body.textContent = "";
    while (keep.firstChild) body.appendChild(keep.firstChild);
  };
  cancel.addEventListener("click", (e) => { e.stopPropagation(); close(); });
  box.addEventListener("click", (e) => e.stopPropagation());
  // Ctrl/⌘+Enter 로 저장. 그냥 Enter 는 줄바꿈이어야 합니다 -- 자막 한 줄이
  // 늘 한 문장은 아닙니다.
  box.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save.click(); }
  });

  save.addEventListener("click", async (e) => {
    e.stopPropagation();
    save.disabled = true;
    const payload = { id: owner, cue: c.id, backend: state.backend };
    if (src.value !== (c.text || "")) payload.text = src.value.trim();
    if (tr.value !== (trOf(c) || "")) payload.tr = tr.value.trim();
    const t0 = +at.value;
    if (Number.isFinite(t0) && Math.abs(t0 - cueStart(c)) > 0.05) payload.start = t0;
    const res = await (await fetch("/api/cue", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })).json();
    if (res.error) { jobError(res.error); save.disabled = false; return; }
    row.classList.remove("editing");
    applyCueEdit(res.cue);
  });

  del.addEventListener("click", async (e) => {
    e.stopPropagation();
    del.disabled = true;
    const res = await (await fetch("/api/cue/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: owner, cue: c.id }),
    })).json();
    if (res.error) { jobError(res.error); del.disabled = false; return; }
    row.classList.remove("editing");
    dropCue(c.id);
  });
}

function mkbtn(text, cls) {
  const b = document.createElement("button");
  b.className = "seg " + cls;
  b.textContent = text;
  return b;
}

/* 지금 보고 있는 것을 목록이 쓰는 값으로. 서버가 그것으로 표를 찾습니다. */
function editOwner() {
  if (state.live) return "live:" + state.live.id;
  if (state.doc && !isLiveDoc()) return state.doc.id;
  return "";
}

/* 서버가 돌려준 줄을 화면의 기억과 맞춥니다. 라이브는 `t`, 녹화본은
 * `start` 로 들고 있어서 여기서 한 번 맞춰 줍니다. */
function applyCueEdit(got) {
  const c = state.cues.find(x => x.id === got.id);
  if (!c) return;
  Object.assign(c, {
    text: got.text, lang: got.lang, speaker: got.speaker,
    edited: got.edited, translations: got.translations,
    start: got.t, end: got.end,
  });
  if ("t" in c) c.t = got.t;
  const row = $("script").querySelector(`.line[data-id="${CSS.escape(String(got.id))}"]`);
  if (row) refreshScriptRow(row, c);
  renderCue();
}

function dropCue(id) {
  const i = state.cues.findIndex(x => x.id === id);
  if (i >= 0) state.cues.splice(i, 1);
  if (state.live) state.live.byId.delete(id);
  const row = $("script").querySelector(`.line[data-id="${CSS.escape(String(id))}"]`);
  if (row) row.remove();
  state.idx = -1;
  renderCue();
}

/* 라이브에서 「따라가기」는 특정 줄이 아니라 **바닥**을 좇는 것입니다.
 *
 * 예전에는 새 줄에 scrollIntoView({block:"end"})를 걸었습니다. 그런데 줄은
 * 붙은 뒤에도 높이가 계속 바뀝니다 -- 0.2초쯤 뒤에 번역이 도착해 한 줄이
 * 늘고(refreshScriptRow가 `.tr`을 붙입니다), 정제본이 오면 여러 줄이 하나로
 * 합쳐집니다. 그 자리들에서는 다시 맞추지 않았으므로, 맨 아래 줄이 조금씩
 * 화면 밖으로 밀려 잘려 보였습니다.
 *
 * 컨테이너를 바닥에 붙이면 높이가 어떻게 바뀌든 상관이 없습니다. 부드러운
 * 스크롤은 쓰지 않습니다 -- 자막이 몇 백 밀리초마다 들어오므로 애니메이션이
 * 끝나기 전에 다음 것이 시작되어 영영 바닥에 닿지 못합니다. */
function pinScriptToBottom() {
  // 받는 중일 때만 바닥을 좇습니다. 끝난 방송은 녹화본처럼 읽는 것이므로
  // 지금 재생 중인 줄을 가운데에 두는 편이 맞습니다(markScript).
  if (!state.follow || !isLiveDoc() || !isLiveReceiving()) return;
  const box = $("script");
  box.scrollTop = box.scrollHeight;
}

function markScript(i) {
  // 받는 중에는 표시하지 않습니다. 그때는 늘 마지막 줄이고, 스크립트는
  // 이미 바닥에 붙어 있습니다.
  if (isLiveDoc() && isLiveReceiving()) return;
  const box = $("script");
  box.querySelectorAll(".line.on").forEach(el => el.classList.remove("on"));
  if (i < 0) return;
  // 녹화본은 위치(data-i)로, 끝난 라이브는 자막 id(data-id)로 그려져
  // 있습니다. 두 목록의 그리는 방식이 다르므로 찾는 방식도 다릅니다.
  const el = isLiveDoc()
    ? box.querySelector(`.line[data-id="${CSS.escape(String((state.cues[i] || {}).id))}"]`)
    : box.querySelector(`.line[data-i="${i}"]`);
  if (!el) return;
  el.classList.add("on");
  if (state.follow) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

/* 대본 창 모드.
 *
 * `?script=<열쇠>` 로 열면 같은 앱이 대본만 그립니다. 임베드가 막힌 방송
 * (멤버십 등)을 유튜브에서 보면서 이 창으로 대본을 읽는 용도입니다.
 *
 * 별도 페이지를 새로 쓰지 않은 이유가 있습니다. SSE, 정제본 흡수, 번역
 * 도착 처리를 복사하면 고칠 곳이 두 군데가 되고, 그 값을 이미 여러 번
 * 치렀습니다. 여기서는 플레이어를 만들지 않고 나머지 껍데기를 감출
 * 뿐입니다 -- 자막을 받아 그리는 길은 같은 하나입니다. */
function scriptWindowKey() {
  return new URLSearchParams(location.search).get("script") || "";
}

const SCRIPT_WIN = "width=460,height=860,menubar=no,toolbar=no";

function openScriptWindow() {
  const key = state.live ? "live:" + state.live.id
            : (state.doc && !isLiveDoc() ? state.doc.id : "");
  if (!key) { alert("먼저 영상이나 방송을 여십시오."); return; }
  const url = `/?script=${encodeURIComponent(key)}`;
  // 이미 띄워 둔 창이 있으면 그것을 씁니다. 탭 소리로 시작하면 아래에서
  // 미리 열어 두므로, 여기서 새로 열면 빈 창과 대본 창이 따로 남습니다.
  if (state.scriptWin && !state.scriptWin.closed) {
    state.scriptWin.location = url;
    state.scriptWin.focus();
    return;
  }
  state.scriptWin = window.open(url, "mimiwatch-script", SCRIPT_WIN);
}

/* 아직 세션 번호를 모를 때 자리만 잡아 두는 창.
 *
 * 순서 때문에 이렇게 합니다. `window.open` 은 사용자 조작 직후에만 열리는데,
 * 탭 소리는 공유 창을 고르는 데 몇 초가 걸리고 그 사이에 유효기간이
 * 지납니다. 그 뒤에 열려고 하면 크롬이 조용히 막습니다. 그래서 조작이 아직
 * 살아 있는 지점에서 빈 창을 먼저 잡아 두고, 세션이 생기면 그 창을
 * 대본으로 돌립니다. */
function openPendingScriptWindow() {
  const win = window.open("", "mimiwatch-script", SCRIPT_WIN);
  if (!win) return null;          // 팝업 차단
  win.document.write(
    '<!doctype html><meta charset="utf-8"><title>자막 내역</title>'
    + '<style>html{color-scheme:dark light}'
    + 'body{margin:0;display:grid;place-items:center;height:100vh;'
    + 'font:14px/1.7 system-ui,sans-serif;background:#0e1117;color:#8b95a7;'
    + 'text-align:center;padding:2rem}'
    + '@media(prefers-color-scheme:light){body{background:#fff;color:#666}}'
    + '</style><div>공유할 탭을 고르면<br>여기에 자막이 쌓입니다.</div>');
  win.document.close();
  return win;
}

/* 스크립트에서 줄을 누르면 무슨 일이 일어나는가.
 *
 * 「무엇을 보일지」(setScriptView)와는 다른 축입니다. 저쪽은 원문·번역 중
 * 무엇을 그릴지이고, 이쪽은 누르면 어떤 일이 일어날지입니다. */
function setScriptMode(m) {
  state.scriptMode = m;
  const box = $("script");
  box.classList.toggle("mode-edit", m === "edit");
  box.classList.toggle("mode-tr", m === "tr");
  $("tr-bar").hidden = m !== "tr";
  if (m !== "tr") clearPicks();
  else markKeptRows();
  document.querySelectorAll("[data-smode]").forEach(b =>
    b.classList.toggle("on", b.dataset.smode === m));
  // 모드를 옮기면 열려 있던 편집기는 닫습니다. 읽기로 돌아갔는데 편집기가
  // 남아 있으면 그 줄만 규칙이 다른 상태가 됩니다.
  if (m !== "edit") closeAllCueEditors();
}

function closeAllCueEditors() {
  $("script").querySelectorAll(".line.editing .ce-bar button:last-child")
    .forEach(b => b.click());          // 각 편집기의 「취소」
}

/* ---------- 다시 번역할 줄 고르기 ----------
 *
 * 파일 탐색기와 같은 규칙입니다. 누르면 그 줄만 뒤집히고, shift 로 누르면
 * 직전에 누른 줄부터 여기까지가 한꺼번에 들어옵니다. 긴 방송에서 한 대목만
 * 다시 돌리고 싶을 때 한 줄씩 스물세 번 누르게 할 수는 없습니다. */
function pickRow(id, extend) {
  const ids = state.cues.map(c => c.id);
  if (extend && state.pickAnchor != null) {
    const a = ids.indexOf(state.pickAnchor), b = ids.indexOf(id);
    if (a >= 0 && b >= 0) {
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) state.picked.add(ids[i]);
    }
  } else {
    if (state.picked.has(id)) state.picked.delete(id);
    else state.picked.add(id);
    state.pickAnchor = id;
  }
  syncPicks();
}

function clearPicks() {
  state.picked.clear();
  state.pickAnchor = null;
  syncPicks();
}

function pickAll() {
  state.cues.forEach(c => state.picked.add(c.id));
  syncPicks();
}

/* 사람이 고친 번역은 재번역이 건너뜁니다. 고르기 전에 그렇다고 보여 줍니다 --
 * 열두 줄을 골랐는데 둘이 조용히 빠지면 왜 안 바뀌었는지 알 수 없습니다. */
function markKeptRows() {
  state.cues.forEach(c => {
    const row = rowOf(c.id);
    if (row) row.classList.toggle("kept", String(c.edited || "").includes("tr"));
  });
}

const rowOf = (id) =>
  $("script").querySelector(`.line[data-id="${CSS.escape(String(id))}"]`);

function syncPicks() {
  state.cues.forEach(c => {
    const row = rowOf(c.id);
    if (row) row.classList.toggle("picked", state.picked.has(c.id));
  });
  const n = state.picked.size;
  const kept = [...state.picked].filter(id => {
    const c = state.cues.find(x => x.id === id);
    return c && String(c.edited || "").includes("tr");
  }).length;
  $("tr-count").textContent = n === 0 ? "고른 줄 없음"
    : `${n}줄 선택` + (kept ? ` (손으로 고친 ${kept}줄은 건너뜁니다)` : "");
  $("tr-go").disabled = n === 0 || n === kept;
}

async function runRetranslate() {
  const owner = editOwner();
  if (!owner || !state.picked.size) return;
  const ids = state.cues.filter(c => state.picked.has(c.id)).map(c => c.id);
  $("tr-go").disabled = true;
  const res = await (await fetch("/api/retranslate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: owner, backend: state.backend, cues: ids,
                           genre: currentGenre() }),
  })).json();
  if (res.error) { jobError(res.error); $("tr-go").disabled = false; return; }
  await watchRetranslate(res.id, res.kept || 0);
}

/* 진행률은 기존 작업 상자를 그대로 씁니다. 재번역은 줄당 0.15초라 스무 줄만
 * 골라도 몇 초씩 걸리고, 그동안 아무것도 없으면 멈춘 것처럼 보입니다. */
async function watchRetranslate(jobId, kept) {
  const box = $("job");
  box.hidden = false;
  box.classList.remove("error");
  $("job-cancel").disabled = false;
  state.jobId = jobId;
  while (true) {
    await new Promise(r => setTimeout(r, 500));
    const st = await (await fetch(`/api/job/${jobId}`)).json();
    document.querySelector(".job-label").textContent = "다시 번역하는 중…";
    const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
    $("job-fill").style.width = pct + "%";
    $("job-count").textContent = `${st.done}/${st.total}`
      + (kept ? ` · 건너뜀 ${kept}줄(손으로 고침)` : "");
    if (st.state === "error") { jobError(st.error); return; }
    if (st.state === "cancelled") { box.hidden = true; state.jobId = null; return; }
    if (st.state === "done") {
      $("job-count").textContent = `${st.done}줄 다시 번역했습니다`
        + (kept ? ` · 건너뜀 ${kept}줄(손으로 고침)` : "")
        + (st.skipped ? ` · 옮길 것 없음 ${st.skipped}줄` : "");
      setTimeout(() => { box.hidden = true; }, 5000);
      state.jobId = null;
      await reloadCues();
      clearPicks();
      return;
    }
  }
}

/* 다시 번역한 줄을 화면에 되받습니다. 라이브는 SSE 로 이미 왔지만, 녹화본은
 * 흘려보낼 통로가 없으므로 여기서 한 번 더 읽습니다. */
async function reloadCues() {
  if (state.live) { markKeptRows(); return; }
  if (!state.doc || isLiveDoc()) return;
  const doc = await (await fetch(`/api/video/${encodeURIComponent(state.doc.id)}`)).json();
  if (doc.error) return;
  state.cues = doc.cues;
  state.doc.backends_done = doc.backends_done;
  buildScript();
  markKeptRows();
  syncPicks();
  renderBackendPicker();
  renderCue();
}

/* 스크립트 줄에서 무엇을 보일지. 화면 위 자막 모드와는 다른 축입니다 --
 * 저쪽은 영상 위, 이쪽은 대본 자체입니다. */
function setScriptView(v) {
  const box = $("script");
  box.classList.toggle("hide-src", v === "tr");
  box.classList.toggle("hide-tr", v === "src");
  document.querySelectorAll("[data-sview]").forEach(b =>
    b.classList.toggle("on", b.dataset.sview === v));
  const p = loadPrefs(); savePrefs({ ...p, scriptView: v });
  pinScriptToBottom();
}
