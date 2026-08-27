/* mimiwatch player logic.
 *
 * The recorded-video flow needs no clock negotiation: every cue carries a
 * media-relative timestamp from transcription, and the YouTube iframe API
 * reports the same clock through getCurrentTime(). Lining them up is a
 * lookup, not an estimate -- the offset slider exists only for live streams
 * and for a viewer who wants to nudge it by taste.
 */
const $ = (id) => document.getElementById(id);
const state = {
  doc: null, cues: [], idx: -1, player: null, ready: false,
  mode: "both", offset: 0, showPrev: true, follow: true, panelHidden: false,
  backend: "local-m2m100", asr: "local-hayamimi",
  backends: [], asrBackends: [], liveProfiles: [], jobId: null,
  live: null,          // { id, es, byId } while a broadcast is running
};

/* A cue can hold one translation per backend, so switching backends is a
 * lookup when the work has already been done and a job when it has not. */
const trOf = (c) => c && c.translations ? c.translations[state.backend] : null;

/* A speaker chip is only information when it distinguishes someone. CAM++
 * gives one embedding per segment, so a four-way collab mixed into a single
 * stream comes back as S1 for every line -- a label that decorates without
 * telling the reader anything. Hold the chips until a second speaker
 * actually appears, and show them from then on. */
function showSpeakers() {
  const set = state.live ? state.live.speakers
            : new Set(state.cues.map(c => c.speaker).filter(Boolean));
  return !!set && set.size >= 2;
}
const chipFor = (c) => (showSpeakers() && c && c.speaker) ? c.speaker : "";

const PREFS = "mimiwatch.prefs";
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS)) || {}; }
  catch { return {}; }        // private windows and blocked storage both land here
}
function savePrefs(p) {
  try { localStorage.setItem(PREFS, JSON.stringify(p)); } catch { /* non-fatal */ }
}

/* ---------- cue lookup ---------- */
// Cues are sorted and non-overlapping, so a walking index beats a binary
// search here: playback advances by ~0.1s per tick and almost always lands
// on the same cue or the next one.
function cueAt(t) {
  const c = state.cues;
  if (!c.length) return -1;

  // Live subtitles cannot be looked up the way recorded ones are. A line is
  // published a few seconds after the words were spoken, so by the time it
  // exists the player has already moved past its timestamp -- a strict
  // window would show nothing, always. Hold the newest line that has started
  // until the next one takes over, which is how live captioning reads
  // anyway. The offset slider still shifts the whole track.
  if (state.live) {
    // Media time is the wrong axis here. A refined line carries the START of
    // the group it absorbed, which can be half a minute behind the player,
    // so ordering by timestamp would bury the line that just arrived. What
    // the viewer wants is the most recent thing recognised, held until the
    // next one lands or the speaker falls silent.
    const i = c.length - 1;
    const age = (Date.now() - (c[i].arrived || 0)) / 1000;
    return age > 20 ? -1 : i;
  }

  let i = state.idx >= 0 ? state.idx : 0;
  while (i > 0 && c[i].start > t) i--;
  while (i < c.length - 1 && c[i + 1].start <= t) i++;
  if (t < c[i].start) return -1;
  // Hold the line a moment past its end: a subtitle that blinks out during
  // the natural pause after a sentence reads as a dropped caption.
  if (t > c[i].end + 1.2) return -1;
  return i;
}

function renderCue() {
  if (!state.player || !state.ready) return;
  const t = state.player.getCurrentTime() + state.offset;
  const i = cueAt(t);
  const cur = i >= 0 ? state.cues[i] : null;
  const prev = i > 0 ? state.cues[i - 1] : null;

  // A cue that carries no translation (a fragment the model would only have
  // damaged) belongs to the source view. Showing it inside "번역만" or "둘 다"
  // reads as a line that failed to translate rather than one that never
  // needed to.
  const line = (c) => {
    if (!c) return "";
    if (state.mode === "source") return c.text;
    if (state.mode === "off") return "";
    return trOf(c) || "";
  };

  wrap($("cue-main"), line(cur), chipFor(cur));
  wrap($("cue-src"), state.mode === "both" && trOf(cur) ? cur.text : "");
  wrap($("cue-prev"), state.showPrev ? line(prev) : "", chipFor(prev));

  if (i !== state.idx) {
    state.idx = i;
    markScript(i);
  }
}
function wrap(el, text, speaker) {
  el.textContent = "";
  if (!text) return;
  const s = document.createElement("span");
  if (speaker) {
    const chip = document.createElement("b");
    chip.className = "spk-chip";
    chip.textContent = speaker;
    s.appendChild(chip);
  }
  s.appendChild(document.createTextNode(text));
  el.appendChild(s);
}

/* ---------- script panel ---------- */
function buildScript() {
  const box = $("script");
  box.textContent = "";
  state.cues.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "line";
    row.dataset.i = i;
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = fmt(c.start);
    const body = document.createElement("div");
    const tx = document.createElement("div");
    tx.className = "tx";
    tx.textContent = c.text;
    body.appendChild(tx);
    const trText = trOf(c);
    if (trText) {
      const tr = document.createElement("div");
      tr.className = "tr";
      tr.textContent = trText;
      body.appendChild(tr);
    }
    row.append(t, body);
    row.addEventListener("click", () => {
      if (state.player) { state.player.seekTo(c.start, true); state.player.playVideo(); }
    });
    box.appendChild(row);
  });
}
function refreshScriptRow(row, c) {
  const body = row.lastElementChild;
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
    body.appendChild(tr);
  }
  row.classList.toggle("pending", c.kind === "final" && !trText);
}

function appendScriptLine(c) {
  const box = $("script");
  const existing = box.querySelector(`.line[data-id="${c.id}"]`);
  if (existing) { refreshScriptRow(existing, c); return; }
  const row = document.createElement("div");
  row.className = "line";
  row.dataset.id = c.id;
  const t = document.createElement("div");
  t.className = "t";
  t.textContent = fmt(c.start);
  const body = document.createElement("div");
  row.append(t, body);
  refreshScriptRow(row, c);
  row.addEventListener("click", () => {
    if (state.player) { state.player.seekTo(c.start, true); state.player.playVideo(); }
  });
  box.appendChild(row);
  if (state.follow) row.scrollIntoView({ block: "end", behavior: "smooth" });
}

function markScript(i) {
  if (state.live) return;   // live rows are keyed by cue id, not position
  const box = $("script");
  box.querySelectorAll(".line.on").forEach(el => el.classList.remove("on"));
  if (i < 0) return;
  const el = box.querySelector(`.line[data-i="${i}"]`);
  if (!el) return;
  el.classList.add("on");
  if (state.follow) el.scrollIntoView({ block: "center", behavior: "smooth" });
}
const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/* ---------- language status (R3.8/R3.9) ---------- */
function updateLangStatus() {
  const el = $("lang-status");
  if (!state.doc) { el.textContent = ""; return; }
  const src = state.doc.source_lang, viewer = $("viewer-lang").value;
  if (src === viewer) {
    el.className = "status";
    el.innerHTML = `원본 <b>${src}</b> · 내 언어와 같아 <b>번역 없음</b>`;
  } else if (state.doc.translated && state.doc.viewer_lang === viewer) {
    el.className = "status";
    el.innerHTML = `원본 <b>${src}</b> → <b>${viewer}</b> 번역됨`;
  } else {
    el.className = "status warn";
    el.innerHTML = `원본 <b>${src}</b> · <b>${viewer}</b> 번역본이 없습니다 (재전사 필요)`;
  }
}

/* ---------- loading ---------- */
async function loadVideo(id) {
  const doc = await (await fetch(`/api/video/${id}`)).json();
  state.doc = doc;
  state.cues = doc.cues || [];
  state.idx = -1;
  buildScript();
  updateLangStatus();
  renderBackendPicker();
  applyModeForDoc();
  if (state.player && state.ready) state.player.loadVideoById(id);
  else await createPlayer(id);
}

/* Someone watching a talk in their own language does not turn subtitles on;
 * the script panel already carries the text. So the default follows the
 * language match, and each of the two cases remembers its own last choice. */
function docHasTranslation() {
  if (!state.doc) return false;
  // Same language means no translation will ever come, so subtitles start off.
  if (state.doc.source_lang && state.doc.source_lang === state.doc.viewer_lang) {
    return false;
  }
  // A live session has no cues yet at the moment this first runs; judging it
  // by what has arrived would leave subtitles off for a broadcast that is
  // about to be translated.
  if (state.live) return true;
  return state.cues.some(c => trOf(c));
}

function applyModeForDoc() {
  const translated = docHasTranslation();
  document.querySelectorAll("[data-needs-translation]").forEach(b => {
    b.hidden = !translated;
  });
  const p = loadPrefs();
  state.mode = translated ? (p.modeTranslated || "both") : (p.modeSame || "off");
  if (!translated && (state.mode === "both" || state.mode === "translation")) {
    state.mode = "off";
  }
  document.querySelectorAll(".seg").forEach(b => b.classList.toggle("on", b.dataset.mode === state.mode));
  state.idx = -1;
  renderCue();
}

/* The IFrame API only builds a working player when the constructor is given a
 * videoId -- without one it leaves an empty container and never fires
 * onReady. So the player is created after the first video is known, not
 * before. */
let apiReady = null;
function whenApiReady() {
  if (apiReady) return apiReady;
  apiReady = new Promise((resolve) => {
    if (window.YT && window.YT.Player) return resolve();
    // The API calls this global when it finishes loading; the poll is the
    // safety net for the case where it fired before this script ran.
    window.onYouTubeIframeAPIReady = resolve;
    const timer = setInterval(() => {
      if (window.YT && window.YT.Player) { clearInterval(timer); resolve(); }
    }, 100);
    setTimeout(() => { clearInterval(timer); resolve(); }, 10000);
  });
  return apiReady;
}

async function createPlayer(videoId) {
  await whenApiReady();
  if (!(window.YT && window.YT.Player)) {
    playerError("YouTube IFrame API를 불러오지 못했습니다. 네트워크를 확인해 주세요.");
    return;
  }
  if (state.player) { state.player.loadVideoById(videoId); return; }
  state.player = new YT.Player("player", {
    videoId,
    playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
    events: {
      onReady: () => {
        state.ready = true;
        setInterval(renderCue, 100);
      },
      onError: (e) => playerError(`영상을 재생할 수 없습니다 (code ${e.data}). ` +
                                  "임베드가 차단된 영상일 수 있습니다."),
    },
  });
}

function playerError(msg) {
  const wrap = document.getElementById("player-wrap");
  let box = document.getElementById("player-error");
  if (!box) {
    box = document.createElement("div");
    box.id = "player-error";
    box.className = "player-error";
    wrap.appendChild(box);
  }
  box.textContent = msg;
}

/* ---------- controls ---------- */
function applySize(px) {
  $("overlay").style.fontSize = px + "px";
  $("cue-main").style.fontSize = px + "px";
}
function bind() {
  document.querySelectorAll(".seg").forEach(b => {
    b.addEventListener("click", () => {
      state.mode = b.dataset.mode;
      document.querySelectorAll(".seg").forEach(x => x.classList.toggle("on", x === b));
      persist(); state.idx = -1; renderCue();
    });
  });
  $("size").addEventListener("input", e => { applySize(+e.target.value); persist(); });
  $("dim").addEventListener("input", e => {
    document.documentElement.style.setProperty("--cue-bg", (+e.target.value / 100).toFixed(2));
    persist();
  });
  $("pos").addEventListener("input", e => { $("overlay").style.bottom = e.target.value + "%"; persist(); });
  $("show-prev").addEventListener("change", e => { state.showPrev = e.target.checked; persist(); renderCue(); });
  $("follow").addEventListener("change", e => { state.follow = e.target.checked; });
  $("offset").addEventListener("input", e => {
    state.offset = +e.target.value;
    $("offset-val").textContent = state.offset.toFixed(1) + "s";
    persist();
  });
  $("viewer-lang").addEventListener("change", () => { updateLangStatus(); persist(); });
  $("video-picker").addEventListener("change", e => { stopLive(); loadVideo(e.target.value); });
  $("toggle-panel").addEventListener("click", () => setPanel(!state.panelHidden));
  $("backend-picker").addEventListener("change", e => selectBackend(e.target.value));
  $("open-settings").addEventListener("click", openSettings);
  $("settings-close").addEventListener("click", () => $("settings-dialog").close());
  $("form-back").addEventListener("click", showEngineList);
  $("engine-form").addEventListener("submit", saveEngine);
  document.querySelectorAll("[data-add]").forEach(b =>
    b.addEventListener("click", () => showEngineForm(b.dataset.add, null)));
  $("asr-picker").addEventListener("change", e => {
    state.asr = e.target.value; persist();
  });
  $("job-cancel").addEventListener("click", cancelJob);
  $("add-video").addEventListener("click", () => $("add-dialog").showModal());
  $("add-form").addEventListener("submit", submitAdd);
  $("del-video").addEventListener("click", deleteVideo);
  $("live-stop").addEventListener("click", stopLive);
  // Watching is a full-screen activity; reaching for the mouse to reclaim
  // width breaks it, so the toggle also answers to a key.
  document.addEventListener("keydown", (e) => {
    if (e.key === "s" && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) {
      setPanel(!state.panelHidden);
    }
  });
}

function setPanel(hidden) {
  state.panelHidden = hidden;
  $("layout").classList.toggle("panel-hidden", hidden);
  $("toggle-panel").textContent = hidden ? "스크립트 ◂" : "스크립트 ▸";
  $("toggle-panel").classList.toggle("on", hidden);
  persist();
}
function persist() {
  const translated = !!(state.doc && state.doc.translated);
  const prev = loadPrefs();
  savePrefs({
    ...prev,
    [translated ? "modeTranslated" : "modeSame"]: state.mode,
    size: +$("size").value, dim: +$("dim").value,
    pos: +$("pos").value, showPrev: $("show-prev").checked,
    offset: +$("offset").value, viewerLang: $("viewer-lang").value,
    panelHidden: state.panelHidden, backend: state.backend,
    profile: (document.querySelector('#add-form select[name="profile"]') || {}).value,
    asr: state.asr,
  });
}
function restore() {
  const p = loadPrefs();
  if (p.viewerLang) $("viewer-lang").value = p.viewerLang;
  if (p.size) $("size").value = p.size;
  if (p.dim != null) $("dim").value = p.dim;
  if (p.pos != null) $("pos").value = p.pos;
  if (p.showPrev != null) $("show-prev").checked = p.showPrev;
  if (p.offset != null) { $("offset").value = p.offset; state.offset = p.offset; }
  state.showPrev = $("show-prev").checked;
  applySize(+$("size").value);
  document.documentElement.style.setProperty("--cue-bg", (+$("dim").value / 100).toFixed(2));
  $("overlay").style.bottom = $("pos").value + "%";
  $("offset-val").textContent = state.offset.toFixed(1) + "s";
  setPanel(!!p.panelHidden);
}

/* ---------- translation backends ---------- */
async function loadBackends() {
  const cfg = await (await fetch("/api/backends")).json();
  state.backends = cfg.backends;
  const p = loadPrefs();
  state.liveProfiles = cfg.live_profiles || [];
  renderProfilePicker();
  state.asrBackends = cfg.asr_backends || [];
  const p0 = loadPrefs();
  const asrIds = state.asrBackends.map(b => b.id);
  state.asr = asrIds.includes(p0.asr) ? p0.asr : (cfg.asr_active || "local-hayamimi");
  renderAsrPicker();
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
    o.textContent = b.label + (state.doc && !state.live && !done ? " (미번역)" : "");
    pick.appendChild(o);
  });
  pick.value = state.backend;
  // Nothing to translate when the speaker already uses the viewer's language.
  const same = state.doc && state.doc.source_lang === state.doc.viewer_lang;
  $("backend-field").hidden = !!same;
}

async function selectBackend(id) {
  state.backend = id;
  persist();
  if (state.live) {
    // Nothing to re-translate: lines already on screen keep what they got,
    // and everything from here uses the new backend.
    state.doc.backends_done = [...new Set([...(state.doc.backends_done || []), id])];
    await fetch("/api/live/backend", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: state.live.id, backend: id }),
    }).catch(() => {});
    renderBackendPicker();
    return;
  }
  const done = (state.doc.backends_done || []).includes(id);
  if (!done && state.doc.source_lang !== state.doc.viewer_lang) {
    await runTranslateJob(state.doc.id, id);
  }
  buildScript(); applyModeForDoc(); renderBackendPicker();
}

async function runTranslateJob(video, backend) {
  const box = $("job");
  box.hidden = false; box.classList.remove("error");
  $("job-cancel").disabled = false;
  document.querySelector(".job-label").textContent = "재번역 중…";
  $("job-label") && ($("job-label").textContent = "재번역 중…");
  $("job-source").textContent = "";
  const res = await (await fetch("/api/translate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video, backend }),
  })).json();
  if (res.error) { jobError(res.error); return; }
  state.jobId = res.id;

  while (true) {
    await new Promise(r => setTimeout(r, 700));
    const st = await (await fetch(`/api/job/${res.id}`)).json();
    const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
    $("job-fill").style.width = pct + "%";
    $("job-count").textContent = `${st.done}/${st.total}  (건너뜀 ${st.skipped})`;
    // Say plainly which model produced the lines so far. "폴백 중"에 대한
    // 의심을 숫자로 답합니다.
    const srcEl = $("job-source");
    srcEl.classList.toggle("local", !!st.degraded);
    srcEl.textContent = st.degraded
      ? `원격 응답 없음 (실패 ${st.failures}회) · 로컬 대체 ${st.by_local}건`
      : `원격 번역 ${st.by_remote}건` + (st.by_local ? ` · 로컬 대체 ${st.by_local}건` : "");
    box.classList.toggle("error", !!st.degraded);
    if (st.state === "error") { jobError(st.error); return; }
    if (st.state === "cancelled") {
      $("job-count").textContent = `중단됨 · ${st.done}/${st.total}까지 저장`;
      setTimeout(() => { box.hidden = true; }, 3000);
      const doc = await (await fetch(`/api/video/${video}`)).json();
      state.doc = doc; state.cues = doc.cues; state.idx = -1;
      buildScript(); applyModeForDoc(); renderBackendPicker();
      return;
    }
    if (st.state === "done") {
      $("job-count").textContent = `완료 ${st.done}건 · ${st.elapsed}초`
        + (st.degraded ? `  · 원격 실패 ${st.failures}회, 로컬로 대체됨` : "");
      setTimeout(() => { box.hidden = true; }, 2500);
      const doc = await (await fetch(`/api/video/${video}`)).json();
      state.doc = doc; state.cues = doc.cues; state.idx = -1;
      return;
    }
  }
}
const PHASE_LABEL = {
  probe: "영상 정보 확인", download: "오디오 내려받는 중",
  transcribe: "전사 중", translate: "번역 중", done: "완료",
};

async function submitAdd(e) {
  if (e.submitter && e.submitter.value === "cancel") return;
  const f = e.target;
  const url = f.url.value.trim();
  if (!url) return;
  // Which pipeline a URL belongs to is the server's call, not the user's.
  const probe = await (await fetch("/api/probe", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  })).json();
  if (probe.error) { jobError(probe.error); return; }
  f.url.value = "";

  if (probe.is_live) {
    await startLive(url, f.lang.value || null, probe);
    return;
  }
  const res = await (await fetch("/api/transcribe", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url, lang: f.lang.value || null, asr: state.asr,
      speakers: f.speakers.checked,
      viewer_lang: $("viewer-lang").value, backend: state.backend,
    }),
  })).json();
  if (res.error) { jobError(res.error); return; }
  await watchTranscribe(res.id);
}

async function watchTranscribe(jobId) {
  const box = $("job");
  state.jobId = jobId;
  box.hidden = false; box.classList.remove("error");
  $("job-cancel").disabled = false;
  $("job-source").textContent = "";

  while (true) {
    await new Promise(r => setTimeout(r, 800));
    const st = await (await fetch(`/api/job/${jobId}`)).json();
    document.querySelector(".job-label").textContent =
      (PHASE_LABEL[st.phase] || st.phase) + (st.title ? ` · ${st.title.slice(0, 28)}` : "");
    // The download reports no numbers, so an indeterminate bar is honest
    // where a 0% bar would look stuck.
    const pct = st.total ? Math.round(st.done / st.total * 100) : null;
    $("job-fill").style.width = pct === null ? "12%" : pct + "%";
    $("job-count").textContent = st.total
      ? `${st.done}/${st.total}` + (st.phase === "transcribe" ? "초" : "")
      : "…";
    if (st.asr_fallback) {
      $("job-source").classList.add("local");
      $("job-source").textContent = "외부 전사 엔진 실패 · hayamimi 로컬로 대체";
    } else if (st.phase === "translate" && (st.by_remote || st.by_local)) {
      $("job-source").classList.toggle("local", !!st.degraded);
      $("job-source").textContent = st.degraded
        ? `원격 응답 없음 · 로컬 대체 ${st.by_local}건`
        : `원격 번역 ${st.by_remote}건`;
    }
    if (st.state === "error") { jobError(st.error); return; }
    if (st.state === "cancelled") {
      $("job-count").textContent = "중단됨";
      setTimeout(() => { box.hidden = true; }, 3000);
      await refreshVideoList(st.video);
      return;
    }
    if (st.state === "done") {
      $("job-count").textContent = `완료 · ${st.elapsed}초`;
      setTimeout(() => { box.hidden = true; }, 2500);
      await refreshVideoList(st.video);
      return;
    }
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
  $("settings-dialog").showModal();
}

function showEngineList() {
  $("engine-form").hidden = true;
  $("settings-body").hidden = false;
  renderEngineList("asr");
  renderEngineList("tr");
}

const LOCKED = { asr: "local-hayamimi", tr: "local-m2m100" };

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

function renderAsrPicker() {
  const sel = $("asr-picker");
  sel.textContent = "";
  state.asrBackends.forEach(b => {
    const o = document.createElement("option");
    o.value = b.id; o.textContent = b.label || b.id;
    sel.appendChild(o);
  });
  sel.value = state.asrBackends.some(b => b.id === state.asr) ? state.asr : LOCKED.asr;
  state.asr = sel.value;
}

/* ---------- live ---------- */
async function startLive(url, lang, probe) {
  stopLive();
  const res = await (await fetch("/api/live/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url, lang, viewer_lang: $("viewer-lang").value, backend: state.backend,
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
  state.live = { id: res.id, byId: new Map(), es: null, speakers: new Set() };
  buildScript();
  renderBackendPicker();
  applyModeForDoc();
  addLiveToPicker(probe);
  $("job").hidden = true;      // a stale re-translation box is not this session's
  state.jobId = null;
  $("live-badge").hidden = false;
  $("offset-wrap").style.display = "flex";   // live needs the nudge
  await createPlayer(probe.id);

  const es = new EventSource(`/api/live/events/${res.id}`);
  state.live.es = es;
  es.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "cue") onLiveCue(m);
    else if (m.type === "translation") onLiveTranslation(m);
    else if (m.type === "status") onLiveStatus(m);
  };
  es.onerror = () => { $("lang-status").innerHTML = "라이브 연결 끊김"; };
}

function addLiveToPicker(probe) {
  // The picker was still naming whichever recording was open, while the
  // screen showed a broadcast. A live session is not a saved video, so it
  // gets a temporary entry that goes away when the session ends.
  const pick = $("video-picker");
  pick.querySelectorAll("option[data-live]").forEach(o => o.remove());
  const o = document.createElement("option");
  o.value = probe.id;
  o.dataset.live = "1";
  o.textContent = `● LIVE  ${(probe.title || "").slice(0, 58)}`;
  pick.prepend(o);
  pick.value = probe.id;
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
  renderCue();
}

function onLiveStatus(m) {
  const el = $("lang-status");
  if (m.state === "error") {
    el.className = "status warn";
    el.textContent = m.error || "라이브 오류";
    stopLive();
    return;
  }
  el.className = "status";
  const src = m.source_lang || "auto";
  el.innerHTML = `${m.state} · 원본 <b>${src}</b> → <b>${m.viewer_lang}</b>`
    + (m.lines ? ` · ${m.lines}줄` : "");
}

function stopLive() {
  const live = state.live;
  if (!live) return;
  if (live.es) live.es.close();
  fetch("/api/live/stop", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: live.id }),
  }).catch(() => {});
  state.live = null;
  $("live-badge").hidden = true;
  $("offset-wrap").style.display = "";
  $("video-picker").querySelectorAll("option[data-live]").forEach(o => o.remove());
}

async function deleteVideo() {
  if (!state.doc) return;
  if (state.live) { alert("라이브 세션은 삭제할 수 없습니다. 중단 후 다시 시도하십시오."); return; }
  const title = state.doc.title.slice(0, 50);
  if (!confirm(`'${title}' 전사를 삭제할까요?\n내려받은 오디오도 함께 지웁니다.`)) return;
  const res = await (await fetch("/api/video/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: state.doc.id }),
  })).json();
  if (res.error) { alert(res.error); return; }
  const list = await (await fetch("/api/videos")).json();
  if (!list.length) { location.reload(); return; }
  await refreshVideoList(list[0].id);
}

async function refreshVideoList(selectId) {
  const list = await (await fetch("/api/videos")).json();
  const pick = $("video-picker");
  pick.textContent = "";
  list.forEach(v => {
    const o = document.createElement("option");
    o.value = v.id;
    const mins = v.duration ? `${Math.round(v.duration / 60)}분` : "";
    o.textContent = `${v.title.slice(0, 62)}  ·  ${v.source_lang}`
      + (v.translated ? `→${v.viewer_lang}` : "") + (mins ? `  ·  ${mins}` : "");
    pick.appendChild(o);
  });
  if (selectId && list.some(v => v.id === selectId)) {
    pick.value = selectId;
    await loadVideo(selectId);
  }
}

async function cancelJob() {
  if (!state.jobId) return;
  await fetch("/api/job/cancel", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: state.jobId }),
  });
  $("job-cancel").disabled = true;
  document.querySelector(".job-label").textContent = "중단하는 중…";
}


function jobError(msg) {
  const box = $("job");
  box.classList.add("error");
  box.querySelector(".job-label").textContent = "재번역 실패";
  $("job-count").textContent = msg;
  setTimeout(() => { box.hidden = true; }, 6000);
}


(async function init() {
  restore(); bind();
  await loadBackends();
  const list = await (await fetch("/api/videos")).json();
  if (!list.length) {
    $("video-picker").innerHTML = "<option>＋ 영상 추가로 시작하세요</option>";
    return;
  }
  await refreshVideoList(list[0].id);
})();
