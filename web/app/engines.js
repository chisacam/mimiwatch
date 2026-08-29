/* mimiwatch 화면 — 엔진 선택기와 「엔진 관리」 대화상자, 장르·콘텐츠 유형 선택기, 서버 종료.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

/* ---------- translation backends ---------- */
async function loadBackends() {
  applyBackends(await (await fetch("/api/backends")).json());
}

/* 받아 온 설정을 화면에 앉힙니다. 시작할 때는 세 요청을 나란히 보내므로
 * 받는 것과 적용하는 것을 나눠 두어야 합니다. */
function applyBackends(cfg) {
  state.backends = cfg.backends;
  const p = loadPrefs();
  state.liveProfiles = cfg.live_profiles || [];
  renderProfilePicker();
  state.genres = cfg.genres || [];
  renderGenrePicker();
  state.asrBackends = cfg.asr_backends || [];
  const p0 = loadPrefs();
  const asrIds = state.asrBackends.map(b => b.id);
  state.asr = asrIds.includes(p0.asr) ? p0.asr : (cfg.asr_active || "tcpp-best");
  if (p0.refine != null) state.refine = !!p0.refine;
  document.querySelector('#add-form input[name="refine"]').checked = state.refine;
  renderAsrPicker();
  booted = true;      // 여기부터는 state가 설정과 맞춰졌으므로 저장해도 됩니다
  const ids = cfg.backends.map(b => b.id);
  // A remembered backend can disappear when the config is edited or renamed.
  // Falling back keeps a stale preference from asking the server for a
  // backend that no longer exists.
  state.backend = ids.includes(p.backend) ? p.backend
                : (ids.includes(cfg.active) ? cfg.active : cfg.backends[0].id);
  if (state.backend !== p.backend) persist();
  renderBackendPicker();
}

function renderBackendPicker() {
  const pick = $("backend-picker");
  pick.textContent = "";
  state.backends.forEach(b => {
    const o = document.createElement("option");
    o.value = b.id;
    // "(미번역)" means "this backend has not run over this file yet", which
    // is a statement about a finished transcript. A live session translates
    // as it goes, so the label would be wrong the moment the first line
    // lands.
    const done = state.doc && (state.doc.backends_done || []).includes(b.id);
    o.textContent = b.label + (state.doc && !isLiveDoc() && !done ? " (미번역)" : "");
    pick.appendChild(o);
  });
  pick.value = state.backend;
  // 대화상자 쪽도 같은 값을 가리켜야 합니다. "(미번역)" 표시는 열려 있는
  // 영상에 대한 말이라 대화상자에는 붙이지 않습니다 -- 거기서 고르는 것은
  // 아직 없는 영상의 엔진입니다.
  fillEngineSelect(document.querySelector('#add-form select[name="backend"]'),
                   state.backends, state.backend, LOCKED.tr);
  // Nothing to translate when the speaker already uses the viewer's language.
  const same = state.doc && state.doc.source_lang === state.doc.viewer_lang;
  $("backend-field").hidden = !!same;
}

async function selectBackend(id) {
  state.backend = id;
  persist();
  if (isLiveDoc()) {
    // Nothing to re-translate: lines already on screen keep what they got,
    // and everything from here uses the new backend. A broadcast whose
    // subtitles were stopped has no session left to tell, and it is no saved
    // video either, so the picker is all there is to update.
    state.doc.backends_done = [...new Set([...(state.doc.backends_done || []), id])];
    if (state.live) {
      await fetch("/api/live/backend", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: state.live.id, backend: id }),
      }).catch(() => {});
    }
    renderBackendPicker();
    return;
  }
  const done = (state.doc.backends_done || []).includes(id);
  if (!done && state.doc.source_lang !== state.doc.viewer_lang) {
    await runTranslateJob(state.doc.id, id);
  }
  buildScript(); applyModeForDoc(); renderBackendPicker();
}

/* 장르는 「콘텐츠 유형」과 다른 축입니다. 유형은 발화를 몇 초에 끊을지를
 * 정하고(라이브 전용), 장르는 그 발화를 어떤 어휘로 옮길지를 정합니다 --
 * 녹화본에도 필요합니다. */
function renderGenrePicker() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  if (!sel) return;
  sel.textContent = "";
  state.genres.forEach(g => {
    const o = document.createElement("option");
    o.value = g.id;
    o.textContent = g.label;
    sel.appendChild(o);
  });
  const saved = loadPrefs().genre;
  sel.value = state.genres.some(g => g.id === saved) ? saved : "general";
  sel.onchange = showGenreHint;
  showGenreHint();
}

function showGenreHint() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  const hint = $("genre-hint");
  const g = state.genres.find(x => x.id === (sel || {}).value);
  if (hint && g) hint.textContent = g.hint;
}

function currentGenre() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  return (sel && sel.value) || "general";
}

/* 열려 있는 영상의 장르를 선택기에 되비칩니다. 되비치지 않으면 다른 영상에
 * 마지막으로 고른 값이 남아, 다시 번역할 때 엉뚱한 프롬프트가 갑니다. */
function syncGenreToDoc() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  const g = (state.doc || {}).genre;
  if (sel && g && state.genres.some(x => x.id === g)) {
    sel.value = g;
    showGenreHint();
  }
}

function renderProfilePicker() {
  const sel = document.querySelector('#add-form select[name="profile"]');
  if (!sel) return;
  sel.textContent = "";
  state.liveProfiles.forEach(p => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = `${p.label} · ${p.max_speech}초마다 끊기`;
    sel.appendChild(o);
  });
  const saved = loadPrefs().profile;
  sel.value = state.liveProfiles.some(p => p.id === saved) ? saved : "broadcast";
}

/* ---------- engine manager ----------
 * Both engine kinds are managed in one place, outside the add-video flow.
 * Putting the transcription picker inside that dialog meant the only way to
 * delete an engine was to pretend to add a video. */
function openSettings() {
  showEngineList();
  loadModels();        // 열 때마다 새로 읽습니다. 파일을 손으로 넣었을 수 있습니다
  if (!$("settings-dialog").open) $("settings-dialog").showModal();
}

/* 서버를 명시적으로 끕니다.
 *
 * 프로세스를 죽이는 것과 다릅니다. 서버가 받는 중인 방송을 먼저 닫아
 * 「종료됨」으로 기록한 뒤에 멈추므로, 사용자가 스스로 끈 것과 서버가 죽은
 * 것이 지난 방송 목록에서 구분됩니다. */
async function shutdownServer() {
  // 이 탭이 라이브를 보고 있지 않아도 서버는 받고 있을 수 있습니다 -- 다른
  // 탭에서 시작했거나, 이 탭을 열기 전부터 돌고 있었거나. 그래서 여기서는
  // 문구만 고르고, 실제로 몇 건을 닫았는지는 응답에서 받습니다.
  const msg = state.live
    ? "받는 중인 방송을 닫고 서버를 종료합니다. 여기까지 받아 적은 자막은 남습니다.\n\n계속할까요?"
    : "서버를 종료합니다. 받는 중인 방송이 있으면 함께 닫습니다.\n\n계속할까요?";
  if (!confirm(msg)) return;

  const btn = $("shutdown");
  const hint = $("shutdown-group").querySelector(".hint");
  btn.disabled = true;
  btn.textContent = "종료하는 중…";

  let stopped = null;                     // null = 답을 못 받음
  try {
    const r = await fetch("/api/shutdown", { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    stopped = (await r.json()).sessions_stopped || 0;
  } catch (e) {
    // 답이 없다고 종료된 것은 아닙니다. 서버가 정말 멈췄으면 다음 요청도
    // 실패하고, 살아 있으면 답합니다 -- 물어보고 나서 적습니다.
    //
    // 예전에는 여기서 곧바로 "종료됨"이라고 적었는데, 그러면 서버가 이
    // 경로를 모르는 판(구버전이 돌고 있어 404가 나는 경우)에도 껐다고
    // 말하게 됩니다. 껐다고 믿고 자리를 뜨면 방송은 계속 받아집니다.
    const alive = await fetch("/api/backends", { cache: "no-store" })
      .then(r => r.ok).catch(() => false);
    if (alive) {
      $("shutdown-group").classList.add("done");
      hint.textContent =
        `서버를 멈추지 못했습니다 (${e.message}). 서버가 이 기능을 모르는 ` +
        "예전 판일 수 있습니다. 터미널에서 Ctrl-C 로 끄십시오.";
      btn.disabled = false;
      btn.textContent = "종료";
      return;
    }
  }

  // 여기까지 왔으면 서버는 멈췄습니다. 새로고침해도 돌아올 곳이 없으므로
  // 화면을 그대로 두고 무엇이 끝났는지만 적습니다.
  $("shutdown-group").classList.add("done");
  hint.textContent =
    (stopped ? `방송 ${stopped}건을 닫고 서버를 종료했습니다. `
             : "서버를 종료했습니다. ") +
    "이 탭은 더 이상 갱신되지 않습니다. 다시 켜려면 "
    + (state.models && state.models.frozen ? "mimiwatch 를 다시 실행하십시오." : "터미널에서 ./run.sh.");
  btn.textContent = "종료됨";
  stopLive();
}

function showEngineList() {
  $("engine-form").hidden = true;
  $("model-form").hidden = true;
  $("settings-body").hidden = false;
  renderEngineList("asr");
  renderEngineList("tr");
}

// 지울 수 없는 기본 엔진. 목록에서 사라지면 고를 것이 없어집니다.
const LOCKED = { asr: "tcpp-best", tr: "local-m2m100" };

function enginesOf(kind) {
  return kind === "asr" ? state.asrBackends : state.backends;
}

function renderEngineList(kind) {
  const box = $(kind === "asr" ? "asr-list" : "tr-list");
  box.textContent = "";
  enginesOf(kind).forEach(b => {
    const row = document.createElement("div");
    row.className = "engine-row" + (b.id === LOCKED[kind] ? " locked" : "");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = b.label || b.id;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = b.backend === "openai"
      ? `${b.base_url} · ${b.model}` : "로컬 실행 · 외부 전송 없음";
    name.appendChild(meta);

    const edit = document.createElement("button");
    edit.textContent = "수정";
    edit.disabled = b.backend !== "openai";
    edit.addEventListener("click", () => showEngineForm(kind, b));

    const del = document.createElement("button");
    del.className = "danger";
    del.textContent = "삭제";
    if (b.id === LOCKED[kind]) {
      del.title = "기본 로컬 엔진은 삭제할 수 없습니다";
      del.disabled = true;
    } else {
      del.addEventListener("click", () => removeEngine(kind, b));
    }
    row.append(name, edit, del);
    box.appendChild(row);
  });
}

function showEngineForm(kind, entry) {
  const f = $("engine-form");
  f.dataset.kind = kind;
  f.dataset.editing = entry ? entry.id : "";
  $("form-title").textContent =
    (entry ? "엔진 수정" : "엔진 추가") + (kind === "asr" ? " · 전사" : " · 번역");
  const seed = entry || { label: "", id: "", base_url: "http://localhost:1234",
                          model: kind === "asr" ? "whisper-1" : "",
                          api_key: "", window_s: 240, min_chars: 0 };
  ["label", "id", "base_url", "model", "api_key"].forEach(k => { f[k].value = seed[k] || ""; });
  f.window_s.value = seed.window_s || 240;
  f.min_chars.value = seed.min_chars || 0;
  $("field-window").hidden = kind !== "asr";
  $("field-minchars").hidden = kind !== "tr";
  f.id.readOnly = !!entry;          // the id keys stored translations
  $("settings-body").hidden = true;
  f.hidden = false;
}

async function saveEngine(e) {
  e.preventDefault();
  const f = e.target;
  const kind = f.dataset.kind;
  const entry = {
    id: f.id.value.trim(), label: f.label.value.trim(), backend: "openai",
    base_url: f.base_url.value.trim(), model: f.model.value.trim(),
    api_key: f.api_key.value.trim(),
  };
  if (kind === "asr") entry.window_s = Math.max(30, parseInt(f.window_s.value, 10) || 240);
  else entry.min_chars = Math.max(0, parseInt(f.min_chars.value, 10) || 0);
  if (!entry.id || !entry.base_url || !entry.model) return;

  const url = kind === "asr" ? "/api/asr-backends" : "/api/backends";
  const cfg = await (await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(entry),
  })).json();
  adoptConfig(cfg);
  showEngineList();
}

async function removeEngine(kind, b) {
  if (!confirm(`'${b.label || b.id}' 을(를) 삭제할까요?`)) return;
  const url = kind === "asr" ? "/api/asr-backends/delete" : "/api/backends/delete";
  const cfg = await (await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: b.id }),
  })).json();
  if (cfg.error) { alert(cfg.error); return; }
  adoptConfig(cfg);
  showEngineList();
}

function adoptConfig(cfg) {
  if (cfg.backends) state.backends = cfg.backends;
  if (cfg.asr_backends) state.asrBackends = cfg.asr_backends;
  // A picker still pointing at something that was just deleted would ask the
  // server for an engine that no longer exists.
  if (!state.backends.some(b => b.id === state.backend)) state.backend = LOCKED.tr;
  if (!state.asrBackends.some(b => b.id === state.asr)) state.asr = LOCKED.asr;
  persist();
  renderAsrPicker();
  renderBackendPicker();
}

/* 엔진 선택기는 두 자리에 있습니다 -- 「관리」 메뉴와 「영상 추가」 대화상자.
 *
 * 넣는 순간 그 엔진으로 전사가 시작되므로, 넣기 전에 고를 수 있어야 합니다.
 * 예전에는 헤더에서만 고를 수 있어서, 대화상자를 열어 둔 채로는 바꿀 수
 * 없었습니다. 두 자리가 같은 값을 가리키므로 어느 쪽에서 고르든 같습니다. */
function fillEngineSelect(sel, entries, current, fallback) {
  if (!sel) return current;
  sel.textContent = "";
  entries.forEach(b => {
    const o = document.createElement("option");
    o.value = b.id; o.textContent = b.label || b.id;
    sel.appendChild(o);
  });
  sel.value = entries.some(b => b.id === current) ? current : fallback;
  return sel.value;
}

function renderAsrPicker() {
  state.asr = fillEngineSelect($("asr-picker"), state.asrBackends,
                               state.asr, LOCKED.asr);
  fillEngineSelect(document.querySelector('#add-form select[name="asr"]'),
                   state.asrBackends, state.asr, LOCKED.asr);
}

/* 「관리」에서 고른 엔진을 서버의 기본값으로도 적습니다. 확장은 서버의 기본값으로 세션을
 * 시작하므로, 여기서 적지 않으면 화면에서 무엇을 골랐든 확장은 경량 엔진으로 시작합니다.
 * 실패는 조용히 지나갑니다 -- 화면의 선택은 이미 바뀌었고, 예전 서버(끝점 없음)일 수 있습니다. */
function syncActive(kind, id) {
  fetch("/api/active", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, id }),
  }).then(r => r.ok ? r.json() : null).then(cfg => { if (cfg) adoptConfig(cfg); }).catch(() => {});
}

function setAsr(id) {
  state.asr = id;
  const a = $("asr-picker");
  const b = document.querySelector('#add-form select[name="asr"]');
  if (a) a.value = id;
  if (b) b.value = id;
  persist();
}

function setBackendPickers(id) {
  const a = $("backend-picker");
  const b = document.querySelector('#add-form select[name="backend"]');
  if (a && [...a.options].some(o => o.value === id)) a.value = id;
  if (b) b.value = id;
}

/* ---------- 모델 · 도구 ----------
 * 모델은 프로그램 밖에 둡니다(paths.py). 예전에는 설치 스크립트가 받아 주었고
 * 화면에는 무엇이 있는지 볼 자리가 없었습니다 -- 파일이 없으면 세션이
 * FileNotFoundError 로 끝나고 "./install.sh 를 실행하십시오"라는 말만 남았습니다.
 * 묶음(PyInstaller)으로 받은 사람에게는 그 스크립트조차 없으므로, 여기서 받고
 * 지우고 허깅페이스에서 더 가져옵니다. 진행은 서버가 bus 로 밀어 줍니다. */

function fmtBytes(n) {
  if (!n) return "";
  if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + "GB";
  if (n >= 1e6) return Math.round(n / 1e6) + "MB";
  return Math.max(1, Math.round(n / 1e3)) + "KB";
}

async function loadModels() {
  try { applyModels(await (await fetch("/api/models")).json()); }
  catch (_) { /* 서버가 이 끝점을 모르는 예전 판. 구역은 비어 있습니다. */ }
}

function applyModels(ov) {
  state.models = ov;
  const dir = $("model-dir");
  if (dir) dir.textContent = ov.model_dir || "";
  renderModelList();
  refreshSetupNotice();
}

/* 위쪽 띠. 전사에 반드시 필요한 것(VAD·whisper·M2M-100·ffmpeg)이 하나라도 없으면
 * 세웁니다. 받는 중이면 그 진행을 같은 자리에 적습니다. */
function refreshSetupNotice() {
  const ov = state.models;
  const box = $("setup-notice");
  if (!box || !ov) return;
  if (ov.ready) { box.hidden = true; return; }
  const missing = ov.items.filter(i => i.required && !["ready", "system"].includes(i.state));
  const busy = missing.filter(i => ["downloading", "queued"].includes(i.state));
  const cur = missing.find(i => i.state === "downloading");
  let text;
  if (cur) {
    const pct = cur.total ? Math.round(cur.done / cur.total * 100) + "%" : fmtBytes(cur.done);
    text = `모델을 받는 중입니다 — ${cur.label} ${pct}` + (busy.length > 1 ? ` (남은 것 ${busy.length - 1}개)` : "");
  } else if (busy.length) {
    text = `모델 ${busy.length}개가 내려받기를 기다리고 있습니다.`;
  } else {
    text = (ov.setup_done ? "필요한 것이 아직 없습니다: " : "처음이시군요. 이 기계에 맞는 엔진을 고르십시오. 없는 것: ")
           + missing.map(i => i.label).join(", ");
  }
  $("setup-text").textContent = text;
  $("setup-start").hidden = busy.length > 0;
  $("setup-download").hidden = busy.length > 0 || !ov.setup_done;
  box.hidden = false;
}

function stateLabel(it) {
  switch (it.state) {
    case "ready": return "있음";
    case "system": return "시스템 것 사용";
    case "missing": return "없음";
    case "partial": return `받다 만 것 ${fmtBytes(it.have)}`;
    case "queued": return "기다리는 중";
    case "downloading": return it.total
      ? `${Math.round(it.done / it.total * 100)}% · ${fmtBytes(it.done)} / ${fmtBytes(it.total)}`
      : `${fmtBytes(it.done)} 받음`;
    case "error": return "실패";
    default: return it.state;
  }
}

function renderModelList() {
  const box = $("model-list");
  const ov = state.models;
  if (!box || !ov) return;
  box.textContent = "";
  // 종류별로 묶어 보입니다. 전사 → 번역 → 보조 → 도구 → 목록에 없는 파일.
  const order = { asr: 0, tr: 1, aux: 2, tool: 3, other: 4 };
  const head = { asr: "전사", tr: "번역", aux: "보조", tool: "도구", other: "목록에 없는 파일" };
  const items = [...ov.items].sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9));
  let lastKind = null;
  items.forEach(it => {
    if (it.kind !== lastKind) {
      lastKind = it.kind;
      const h = document.createElement("div");
      h.className = "model-kind";
      h.textContent = head[it.kind] || it.kind;
      box.appendChild(h);
    }
    box.appendChild(modelRow(it));
  });
}

function modelRow(it) {
  const row = document.createElement("div");
  row.className = `engine-row model-row st-${it.state}` + (it.required ? " required" : "");
  row.dataset.model = it.id;
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = it.label + (it.required ? " ·필수" : "");
  const meta = document.createElement("span");
  meta.className = "meta";
  const bits = [stateLabel(it)];
  if (["ready", "system"].includes(it.state)) bits.push(fmtBytes(it.have || it.size));
  else if (it.size && it.state !== "downloading") bits.push(fmtBytes(it.size));
  if (it.purpose) bits.push(it.purpose);
  if (it.state === "system" && it.system) bits.push(it.system);
  if (it.state === "error" && it.error) bits.push(it.error);
  meta.textContent = bits.join(" · ");
  name.appendChild(meta);
  if (it.state === "downloading") {
    const bar = document.createElement("span");
    bar.className = "job-bar model-bar";
    const fill = document.createElement("i");
    fill.style.width = (it.total ? Math.round(it.done / it.total * 100) : 0) + "%";
    bar.appendChild(fill);
    name.appendChild(bar);
  }
  row.appendChild(name);

  const act = document.createElement("button");
  if (["downloading", "queued"].includes(it.state)) {
    act.textContent = "중단";
    act.addEventListener("click", () => postModel("/api/models/cancel", { id: it.id }));
  } else if (["missing", "partial", "error", "system"].includes(it.state)) {
    act.textContent = it.state === "partial" ? "이어 받기"
                    : it.state === "error" ? "다시" : "받기";
    act.addEventListener("click", () => downloadModels([it.id]));
  } else {
    act.textContent = "받기";
    act.disabled = true;
  }
  const del = document.createElement("button");
  del.className = "danger";
  del.textContent = "삭제";
  del.disabled = !["ready", "partial", "error"].includes(it.state) || !(it.have || it.state !== "ready");
  del.addEventListener("click", () => deleteModel(it));
  row.append(act, del);
  return row;
}

async function postModel(url, body) {
  const res = await (await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })).json();
  if (res.error) alert(res.error);
  await loadModels();
  return res;
}

/* `ids` 는 배열이거나 "default"(기본 세트). `open` 이면 대화상자를 열어 진행을
 * 보게 합니다 -- 위쪽 띠의 「기본 모델 받기」가 그렇습니다. 5GB는 몇 분 걸리고,
 * 그 사이 무엇이 오는지 보이지 않으면 멎은 것으로 보입니다. */
async function downloadModels(ids, open = false) {
  const res = await postModel("/api/models/download", { ids });
  if (open && !res.error) openSettings();
}

async function deleteModel(it) {
  const what = it.kind === "tool" ? "도구" : "모델";
  if (!confirm(`'${it.label}' ${what}을(를) 지울까요? (${fmtBytes(it.have || it.size)})\n`
               + "다시 쓰려면 다시 받아야 합니다.")) return;
  await postModel("/api/models/delete", { id: it.id });
}

/* bus 로 온 항목 하나의 새 상태. 목록의 그 항목만 바꿔 넣고, 필수 항목이
 * 갖춰졐는지 다시 봅니다. 목록 밖의 항목(새로 추가된 것)이면 전부 다시 읽습니다. */
function onModelEvent(m) {
  const ov = state.models;
  if (!ov) { loadModels(); return; }
  const i = ov.items.findIndex(x => x.id === m.id);
  if (i < 0) { loadModels(); return; }
  const { type, ...rest } = m;
  ov.items[i] = rest;
  ov.ready = ov.items.every(x => !x.required || ["ready", "system"].includes(x.state));
  const row = document.querySelector(`#model-list .model-row[data-model="${CSS.escape(m.id)}"]`);
  if (row) row.replaceWith(modelRow(rest));
  refreshSetupNotice();
  // 받기가 끝난 모델이 엔진 설정에 항목을 넣었을 수 있습니다(허깅페이스 추가).
  if (rest.state === "ready" && rest.custom) loadBackends();
}

function showModelForm() {
  const f = $("model-form");
  f.reset();
  $("model-form-error").hidden = true;
  $("settings-body").hidden = true;
  $("engine-form").hidden = true;
  f.hidden = false;
}

async function saveModel(e) {
  e.preventDefault();
  const f = e.target;
  const body = {
    kind: f.kind.value, repo: f.repo.value.trim(), file: f.file.value.trim(),
    label: f.label.value.trim(), id: f.id.value.trim(), device: f.device.value,
    token: f.token.value.trim(),
  };
  const btn = f.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const res = await (await fetch("/api/models/add", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })).json();
    if (res.error) {
      const err = $("model-form-error");
      err.textContent = res.error;
      err.hidden = false;
      return;
    }
    await loadModels();
    showEngineList();
  } finally {
    btn.disabled = false;
  }
}

/* ---------- 초기 설정 ----------
 * 사양은 기계마다 다릅니다. 기본은 가벼운 CPU 엔진(SenseVoice Small + M2M-100)이지만
 * GPU 가 있는 기계라면 whisper 와 Gemma 가 훨씬 낫습니다. 첫 실행에 고르게 하고,
 * 고른 조합에 필요한 모델만 받습니다. 예전에는 무거운 쪽이 기본이라 6GB 를 받고서야
 * 이 기계에서는 버겁다는 것을 알았습니다. */

async function openSetup() {
  const opts = await (await fetch("/api/setup")).json();
  state.setupOpts = opts;
  renderSetupChoices("asr", opts.asr, opts.asr_active);
  renderSetupChoices("tr", opts.tr, opts.active);
  const f = $("setup-form");
  f.viewer_lang.value = $("viewer-lang").value;
  syncSetupTotal();
  if (!$("setup-dialog").open) $("setup-dialog").showModal();
}

function renderSetupChoices(kind, list, current) {
  const box = $(kind === "asr" ? "setup-asr" : "setup-tr");
  box.textContent = "";
  list.forEach(o => {
    const lab = document.createElement("label");
    lab.className = "setup-choice";
    const r = document.createElement("input");
    r.type = "radio"; r.name = "setup-" + kind; r.value = o.id;
    r.checked = o.id === current;
    r.addEventListener("change", syncSetupTotal);
    const body = document.createElement("span");
    const name = document.createElement("b");
    name.textContent = o.label;
    const meta = document.createElement("small");
    const bits = [];
    if (o.backend === "openai") bits.push("원격 서버 · 받을 것 없음 · 그 서버가 떠 있어야 합니다");
    else if (o.model) {
      bits.push(o.model.label);
      bits.push(o.model.state === "ready" ? "있음" : `${fmtBytes(o.model.size)} 받음`);
      // M2M-100 은 CTranslate2 를 CPU 로 고정해 씁니다(translate.py).
      bits.push(o.backend === "local" || o.device === "cpu" ? "CPU" : "GPU가 있으면 GPU");
    }
    meta.textContent = bits.join(" · ");
    body.append(name, document.createElement("br"), meta);
    lab.append(r, body);
    box.appendChild(lab);
  });
}

/* 고른 조합으로 새로 받을 용량. 「이대로 시작」 옆에 적어 두면 5GB 를 받게 될지
 * 미리 압니다. */
function syncSetupTotal() {
  const opts = state.setupOpts;
  if (!opts) return;
  const f = $("setup-form");
  const pick = (kind, list) => list.find(o => o.id === (f.elements["setup-" + kind].value));
  const chosen = [pick("asr", opts.asr), pick("tr", opts.tr)];
  let bytes = 0;
  const names = [];
  chosen.forEach(o => {
    if (o && o.model && o.model.state !== "ready" && o.model.state !== "system") {
      bytes += o.model.size || 0; names.push(o.model.label);
    }
  });
  // M2M-100 은 번역 대체 경로가 늘 필요합니다. Gemma 를 골라도 함께 받습니다.
  const m2m = (state.models || { items: [] }).items.find(i => i.id === "m2m100");
  if (m2m && m2m.state !== "ready" && !names.includes(m2m.label)) {
    bytes += m2m.size || 0; names.push(m2m.label + " (번역 대체용)");
  }
  $("setup-total").textContent = bytes
    ? `받을 것: ${names.join(", ")} — 약 ${fmtBytes(bytes)}`
    : "필요한 모델이 다 있습니다. 바로 쓸 수 있습니다.";
}

async function submitSetup(e) {
  if (e.submitter && e.submitter.value === "cancel") return;
  const f = e.target;
  $("viewer-lang").value = f.viewer_lang.value;
  persist();
  const res = await (await fetch("/api/setup", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asr: f.elements["setup-asr"].value,
                           tr: f.elements["setup-tr"].value, download: true }),
  })).json();
  if (res.error) { alert(res.error); return; }
  // 기본 엔진이 바뀌었으니 선택기도 그리로 맞춥니다.
  await loadBackends();
  setAsr(f.elements["setup-asr"].value);
  state.backend = f.elements["setup-tr"].value;
  setBackendPickers(state.backend);
  persist();
  await loadModels();
  if ((res.queued || []).length) openSettings();     // 내려받기 진행을 보여 줍니다
}
