/* mimiwatch 화면 — 배경 작업(전사·번역)의 시작과 진행률 표시.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

async function runTranslateJob(video, backend) {
  const box = $("job");
  box.hidden = false; box.classList.remove("error");
  $("job-cancel").disabled = false;
  document.querySelector(".job-label").textContent = t("jobs.retranslating");
  $("job-label") && ($("job-label").textContent = t("jobs.retranslating"));
  $("job-source").textContent = "";
  const res = await (await fetch("/api/translate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video, backend, genre: currentGenre() }),
  })).json();
  if (res.error) { jobError(res.error); return; }
  trackJob(res.id);

  while (true) {
    await new Promise(r => setTimeout(r, 700));
    const st = await (await fetch(`/api/job/${res.id}`)).json();
    const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
    $("job-fill").style.width = pct + "%";
    $("job-count").textContent = t("jobs.count.skipped",
      { done: st.done, total: st.total, skipped: st.skipped });
    // Say plainly which model produced the lines so far. "폴백 중"에 대한
    // 의심을 숫자로 답합니다.
    const srcEl = $("job-source");
    srcEl.classList.toggle("local", !!st.degraded);
    srcEl.textContent = st.degraded
      ? t("jobs.source.degraded", { failures: st.failures, n: st.by_local })
      : st.by_local
      ? t("jobs.source.remoteAndLocal", { n: st.by_remote, local: st.by_local })
      : t("jobs.source.remote", { n: st.by_remote });
    box.classList.toggle("error", !!st.degraded);
    // 서버가 재시작되면 작업 스레드는 사라지고 상태만 남습니다. 계속 폴링하면
    // 영원히 끝나지 않으므로 종료 상태로 취급합니다.
    if (st.state === "error" || st.state === "interrupted") { jobError(st.error); return; }
    if (st.state === "cancelled") {
      $("job-count").textContent = t("jobs.cancelled.saved",
        { done: st.done, total: st.total });
      setTimeout(() => { box.hidden = true; }, 3000);
      const doc = await (await fetch(`/api/video/${video}`)).json();
      state.doc = doc; state.cues = doc.cues; state.idx = -1;
      buildScript(); applyModeForDoc(); renderBackendPicker();
      return;
    }
    if (st.state === "done") {
      $("job-count").textContent = st.degraded
        ? t("jobs.done.count.degraded",
            { n: st.done, secs: st.elapsed, failures: st.failures })
        : t("jobs.done.count", { n: st.done, secs: st.elapsed });
      setTimeout(() => { box.hidden = true; }, 2500);
      const doc = await (await fetch(`/api/video/${video}`)).json();
      state.doc = doc; state.cues = doc.cues; state.idx = -1;
      return;
    }
  }
}

/* 이 창이 시작해 폴링으로 보고 있는 작업. bus.js 가 같은 작업의 알림을 받아도
 * 두 번 그리지 않게 표시해 둡니다. */
function trackJob(id) {
  state.jobId = id;
  state.jobLocal = true;
}

/* 다른 창(또는 확장)에서 시작한 작업을 같은 상자에 그립니다. 값은 서버가
 * 밀어 주는 스냅샷입니다. 이 창이 제 작업을 보는 중이면 그쪽이 우선입니다. */
function renderForeignJob(st) {
  if (state.jobId && state.jobId !== st.id && state.jobLocal) return;
  const box = $("job");
  if (st.state === "running") {
    state.jobId = st.id;
    state.jobLocal = false;           // 「중단」은 이 id 로 나가되, 폴링은 하지 않습니다
    box.hidden = false;
    box.classList.toggle("error", !!st.degraded);
    $("job-cancel").hidden = false;
    $("job-cancel").disabled = false;
    const label = st.kind === "transcribe"
      ? phaseLabel(st.phase) + (st.title ? ` · ${st.title.slice(0, 28)}` : "")
      : t("jobs.translating");
    box.querySelector(".job-label").textContent = t("jobs.otherWindow", { label });
    const pct = st.total ? Math.round(st.done / st.total * 100) : null;
    $("job-fill").style.width = pct === null ? "12%" : pct + "%";
    $("job-count").textContent = st.total ? `${st.done}/${st.total}` : "…";
    $("job-source").textContent = st.degraded
      ? t("jobs.source.degraded.short", { n: st.by_local }) : "";
    return;
  }
  if (state.jobId !== st.id) return;
  state.jobId = null;
  $("job-count").textContent = st.state === "done"
    ? t("jobs.done.elapsed", { secs: st.elapsed || 0 })
    : st.state === "cancelled" ? t("jobs.cancelled") : (st.error || st.state);
  box.classList.toggle("error", st.state === "error");
  setTimeout(() => { box.hidden = true; }, 3000);
}

/* The table holds keys, not text: a job outlives a language change, and the
 * phase that arrives from the server is an id either way. */
const PHASE_KEY = {
  probe: "jobs.phase.probe", download: "jobs.phase.download",
  convert: "jobs.phase.convert",
  transcribe: "jobs.phase.transcribe", translate: "jobs.phase.translate",
  done: "jobs.phase.done",
};

function phaseLabel(phase) {
  return PHASE_KEY[phase] ? t(PHASE_KEY[phase]) : (phase || "");
}

async function submitAdd(e) {
  if (e.submitter && e.submitter.value === "cancel") return;
  const f = e.target;

  // 탭 소리는 받을 주소가 없습니다. probe도 건너뜁니다 -- 무엇을 듣고 있는지
  // 아는 것은 사용자뿐이고, 서버는 그 탭에 닿을 수 없습니다.
  if (f.source.value === "tab") {
    const title = f.tab_title.value.trim();
    f.tab_title.value = "";
    await startTabCapture(title, f.lang.value || null);
    return;
  }

  // 로컬 파일: 선택기는 경로를 알려 주지 않으므로 몸통째 올리고, 서버가 놓아 둔
  // 경로로 여느 녹화본과 같은 전사를 시작합니다. 경로를 아는 파일은 주소 칸에
  // 경로를 붙여 넣으면 이 사본 없이 그 자리에서 읽습니다.
  if (f.source.value === "file") {
    const media = f.media.files[0];
    if (!media) { jobError(t("jobs.error.noFile")); return; }
    const up = await uploadLocalFile(media);
    if (up.error) { jobError(up.error); return; }
    f.media.value = "";
    const res = await (await fetch("/api/transcribe", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: up.path, lang: f.lang.value || null, asr: state.asr,
        speakers: f.speakers.checked, refine: state.refine, genre: currentGenre(),
        viewer_lang: $("viewer-lang").value, backend: state.backend,
      }),
    })).json();
    if (res.error) { jobError(res.error); return; }
    await watchTranscribe(res.id);
    return;
  }

  const url = f.url.value.trim();
  if (!url) { jobError(t("jobs.error.noUrl")); return; }
  // Which pipeline a URL belongs to is the server's call, not the user's.
  const probe = await (await fetch("/api/probe", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  })).json();
  if (probe.error) { jobError(probe.error); return; }
  f.url.value = "";

  if (f.dataset.mode === "tile") {
    // 「＋ 타일」: 지금 보는 방송 옆에 붙입니다. 라이브만 됩니다 -- 녹화본은 전사가
    // 끝나야 볼 것이 생기므로 옆에 두고 볼 것이 아닙니다.
    if (!probe.is_live) { jobError(t("jobs.error.tileLiveOnly")); return; }
    await addTile(url, f.lang.value || null, probe);
    return;
  }
  if (probe.is_live) {
    await startLive(url, f.lang.value || null, probe);
    return;
  }
  const res = await (await fetch("/api/transcribe", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url, lang: f.lang.value || null, asr: state.asr,
      speakers: f.speakers.checked, refine: state.refine, genre: currentGenre(),
      viewer_lang: $("viewer-lang").value, backend: state.backend,
    }),
  })).json();
  if (res.error) { jobError(res.error); return; }
  await watchTranscribe(res.id);
}

/* 「주소에서 받기」와 「다른 탭 소리」는 필요한 것이 다릅니다. 쓰지 않는 칸을
 * 남겨 두면 무엇을 채워야 하는지가 흐려지므로 그때그때 바꿉니다. */
function setAddSource(v) {
  const tab = v === "tab", file = v === "file";
  $("url-field").hidden = tab || file;
  $("tab-title-field").hidden = !tab;
  $("file-field").hidden = !file;
  $("source-hint").textContent = tab ? t("jobs.source.hint.tab")
    : file ? t("jobs.source.hint.file")
    : t("jobs.source.hint.url");
  // 녹화본은 주소·파일로만 만듭니다. 탭 소리는 언제나 라이브입니다.
  document.querySelector('#add-form input[name="speakers"]')
    .closest("label").hidden = tab;
}

/* 파일 몸통을 그대로 POST 합니다. fetch 로는 올리는 진행률을 볼 수 없어
 * XHR 입니다 -- 몇 GB 짜리 wav 를 올리는 동안 화면이 죽은 것처럼 보이면
 * 안 됩니다. 진행은 전사 작업과 같은 위쪽 상자에 그립니다. */
function uploadLocalFile(file) {
  return new Promise((resolve) => {
    const box = $("job");
    box.hidden = false;
    box.classList.remove("error");
    $("job-cancel").hidden = true;          // 아직 서버 작업이 아니라 중단할 id 가 없습니다
    box.querySelector(".job-label").textContent =
      t("jobs.uploading", { name: file.name.slice(0, 28) });
    $("job-source").textContent = "";
    const done = (res) => { $("job-cancel").hidden = false; resolve(res); };
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload?name=" + encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      $("job-fill").style.width = Math.round(e.loaded / e.total * 100) + "%";
      $("job-count").textContent = `${Math.round(e.loaded / 1e6)}/${Math.round(e.total / 1e6)}MB`;
    };
    xhr.onload = () => {
      try { done(JSON.parse(xhr.responseText)); }
      catch { done({ error: t("jobs.error.uploadReply") }); }
    };
    xhr.onerror = () => done({ error: t("jobs.error.upload") });
    xhr.send(file);
  });
}

async function watchTranscribe(jobId) {
  const box = $("job");
  trackJob(jobId);
  box.hidden = false; box.classList.remove("error");
  $("job-cancel").disabled = false;
  $("job-source").textContent = "";

  while (true) {
    await new Promise(r => setTimeout(r, 800));
    const st = await (await fetch(`/api/job/${jobId}`)).json();
    document.querySelector(".job-label").textContent =
      phaseLabel(st.phase) + (st.title ? ` · ${st.title.slice(0, 28)}` : "");
    // The download reports no numbers, so an indeterminate bar is honest
    // where a 0% bar would look stuck.
    const pct = st.total ? Math.round(st.done / st.total * 100) : null;
    $("job-fill").style.width = pct === null ? "12%" : pct + "%";
    $("job-count").textContent = !st.total ? "…"
      : st.phase === "transcribe"
      ? t("jobs.count.seconds", { done: st.done, total: st.total })
      : `${st.done}/${st.total}`;
    if (st.asr_fallback) {
      $("job-source").classList.add("local");
      $("job-source").textContent = t("jobs.source.asrFallback");
    } else if (st.phase === "translate" && (st.by_remote || st.by_local)) {
      $("job-source").classList.toggle("local", !!st.degraded);
      $("job-source").textContent = st.degraded
        ? t("jobs.source.degraded.short", { n: st.by_local })
        : t("jobs.source.remote", { n: st.by_remote });
    }
    if (st.state === "error" || st.state === "interrupted") { jobError(st.error); return; }
    if (st.state === "cancelled") {
      $("job-count").textContent = t("jobs.cancelled");
      setTimeout(() => { box.hidden = true; }, 3000);
      await refreshVideoList(st.video);
      return;
    }
    if (st.state === "done") {
      $("job-count").textContent = t("jobs.done.elapsed", { secs: st.elapsed });
      setTimeout(() => { box.hidden = true; }, 2500);
      await refreshVideoList(st.video);
      return;
    }
  }
}

async function cancelJob() {
  if (!state.jobId) return;
  await fetch("/api/job/cancel", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: state.jobId }),
  });
  $("job-cancel").disabled = true;
  document.querySelector(".job-label").textContent = t("jobs.cancelling");
}

/* 실패를 위쪽 막대에 띄웁니다.
 *
 * 예전에는 hidden을 풀지 않아서, 진행 상자가 이미 떠 있을 때만 보였습니다.
 * 주소를 잘못 넣는 것처럼 시작도 못 한 실패는 그래서 조용히 묻혔습니다.
 * 라벨도 "재번역 실패"로 고정되어 있었는데, 이 함수는 전사·주소 해석
 * 실패에도 쓰입니다. */
function jobError(msg) {
  const box = $("job");
  box.hidden = false;
  box.classList.add("error");
  box.querySelector(".job-label").textContent = t("jobs.failed");
  $("job-fill").style.width = "0%";
  $("job-count").textContent = msg;
  $("job-source").textContent = "";
  $("job-cancel").hidden = true;
  setTimeout(() => { box.hidden = true; $("job-cancel").hidden = false; }, 8000);
}
