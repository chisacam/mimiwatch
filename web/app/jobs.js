/* mimiwatch front end — starting background jobs (transcription, translation)
 * and showing their progress.
 *
 * One of the files web/app.js was split into by concern. They are all plain
 * <script>s, read in the order index.html writes them down, and they share one
 * global scope -- module syntax is avoided here for the same reason as in
 * overlay.js, which is shared with the extension. Everything they call in each
 * other is a function call made at run time, so the file order only has to put
 * main.js last. */

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
    // Say plainly which model produced the lines so far. It answers the
    // suspicion behind "falling back" with numbers.
    const srcEl = $("job-source");
    srcEl.classList.toggle("local", !!st.degraded);
    srcEl.textContent = st.degraded
      ? t("jobs.source.degraded", { failures: st.failures, n: st.by_local })
      : st.by_local
      ? t("jobs.source.remoteAndLocal", { n: st.by_remote, local: st.by_local })
      : t("jobs.source.remote", { n: st.by_remote });
    box.classList.toggle("error", !!st.degraded);
    // When the server restarts, the job thread is gone and only its state is
    // left behind. Polling on would never end, so treat it as a final state.
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

/* The job this window started and is watching by polling. It is marked so
 * that bus.js does not draw the same job a second time when an announcement
 * for it arrives. */
function trackJob(id) {
  state.jobId = id;
  state.jobLocal = true;
}

/* Draws a job started in another window (or in the extension) in the same box.
 * The numbers are the snapshot the server pushes. If this window is watching a
 * job of its own, that one wins. */
function renderForeignJob(st) {
  if (state.jobId && state.jobId !== st.id && state.jobLocal) return;
  const box = $("job");
  if (st.state === "running") {
    state.jobId = st.id;
    state.jobLocal = false;           // "Stop" goes out with this id, but we do not poll
    box.hidden = false;
    box.classList.toggle("error", !!st.degraded);
    $("job-cancel").hidden = false;
    $("job-cancel").disabled = false;
    const label = st.kind === "transcribe"
      ? phaseLabel(st.phase) + (st.title ? ` · ${st.title.slice(0, 28)}` : "")
      : st.kind === "burn"
      ? t("jobs.burning")
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

  // The microphone has no URL and no probe either, and unlike a tab it has no
  // title to read off the stream -- only the user knows what room this is.
  if (f.source.value === "mic") {
    const title = f.tab_title.value.trim();
    f.tab_title.value = "";
    await startMicCapture(title, f.lang.value || null);
    return;
  }

  // Tab audio has no URL to fetch. It skips the probe too -- only the user
  // knows what they are listening to, and the server cannot reach that tab.
  if (f.source.value === "tab") {
    const title = f.tab_title.value.trim();
    f.tab_title.value = "";
    await startTabCapture(title, f.lang.value || null);
    return;
  }

  // A local file: the picker never reveals a path, so the body goes up whole
  // and the transcription starts from where the server put it, exactly as for
  // any other VOD. A file whose path is known can be pasted into the URL field
  // instead and is read where it lies, without this copy.
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
        speakers: f.speakers.checked, speaker_solo: f.speaker_solo.checked,
        speaker_threshold: f.speaker_threshold.value || "",
        refine: state.refine, genre: currentGenre(),
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
    // "⊞ Add tile": puts it beside the stream being watched. Live only -- a VOD
    // has nothing to show until its transcription finishes, so it is not
    // something to keep alongside.
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
      speakers: f.speakers.checked, speaker_solo: f.speaker_solo.checked,
      speaker_threshold: f.speaker_threshold.value || "",
      refine: state.refine, genre: currentGenre(),
      viewer_lang: $("viewer-lang").value, backend: state.backend,
      site: probe.site || "", channel: probe.channel || "",
      channel_name: f.channel_name.value.trim(),
    }),
  })).json();
  f.channel_name.value = "";
  if (res.error) { jobError(res.error); return; }
  await watchTranscribe(res.id);
}

/* "From a URL" and "Audio from another tab" need different things. Leaving the
 * unused field standing blurs what has to be filled in, so the fields change
 * with the source. */
function setAddSource(v) {
  const tab = v === "tab", file = v === "file", mic = v === "mic";
  const pushed = tab || mic;          // sound the browser uploads: no URL, no probe
  $("url-field").hidden = pushed || file;
  $("tab-title-field").hidden = !pushed;
  $("file-field").hidden = !file;
  $("channel-field").hidden = pushed || file;
  $("source-hint").textContent = tab ? t("jobs.source.hint.tab")
    : mic ? t("jobs.source.hint.mic")
    : file ? t("jobs.source.hint.file")
    : t("jobs.source.hint.url");
  // A VOD only ever comes from a URL or a file, and speaker labels are a VOD
  // feature. A pushed session is always live, so the box would promise labels
  // that cannot arrive -- for a microphone the labels come from transcribing
  // the recording afterwards, which the hint says.
  document.querySelector('#add-form input[name="speakers"]')
    .closest("label").hidden = pushed;
  syncSpeakerOptions();
}

/* The solo/threshold row only means anything when the speaker box is ticked.
 * It is reset to its own state (unticked, 0.45) on every open of the dialog, so
 * nothing carries over from a previous job. */
function syncSpeakerOptions() {
  const f = $("add-form");
  const show = f.speakers.checked && !isPushedSource(f.source.value);
  $("speaker-options").hidden = !show;
  $("speaker-options-hint").hidden = !show;
}
document.querySelector('#add-form input[name="speakers"]')
  .addEventListener("change", syncSpeakerOptions);

/* POSTs the file body as it is. fetch cannot report upload progress, hence XHR
 * -- the page must not look dead while a wav of several GB goes up. The
 * progress is drawn in the same box up top as a transcription job. */
function uploadLocalFile(file) {
  return new Promise((resolve) => {
    const box = $("job");
    box.hidden = false;
    box.classList.remove("error");
    $("job-cancel").hidden = true;          // not a server job yet, so there is no id to stop
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
    // The applied glossary, when the channel has one. "None" is not written --
    // most channels do not have a glossary, and saying so on every job would
    // blur the cases where one is on.
    if (st.phase === "translate" && st.glossary) {
      $("job-source").textContent += " · " +
        t("jobs.glossary", { name: st.glossary, n: st.glossary_terms || 0 });
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

/* Shows a failure in the bar up top.
 *
 * It used to leave hidden alone, so it was only visible when the progress box
 * already stood there. A failure that never got started -- a mistyped URL, say
 * -- was therefore buried in silence. The label was pinned to "Re-translation
 * failed" as well, while this function also serves failures of transcription
 * and of URL resolution. */
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
