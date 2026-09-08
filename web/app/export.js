/* The mimiwatch screen — the subtitle export dialog.
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope -- module
 * syntax is avoided for the same reason as in overlay.js, which is shared with
 * the extension. Everything they call on each other is a function call at run
 * time, so the file order only has to keep main.js last. */

/* ---------- export ----------
 *
 * The server builds the file and sends it down with `Content-Disposition`.
 * There is a reason it is not built in the browser -- the subtitles are on the
 * server already, and what the page has drawn is the one session being watched
 * right now, nothing else. Exporting any row of the library at all means the
 * server has to read it anyway. */
function exportTarget() {
  if (state.live) return { value: "live:" + state.live.id, title: nowTitleText() };
  if (state.doc && !isLiveDoc()) return { value: state.doc.id, title: state.doc.title };
  return null;
}

function nowTitleText() {
  return $("now-title").textContent.trim();
}

function openExport() {
  const tgt = exportTarget();
  if (!tgt) { alert(t("panel.needTarget")); return; }
  const n = state.cues.length;
  $("export-what").textContent = state.live
    ? t("export.what.live", { title: tgt.title, n })
    : t("export.what", { title: tgt.title, n });
  syncExportHint();
  // Burn-in needs the video file itself. A local file is the only source
  // that has one -- a streamed VOD is transcribed from audio, and the video
  // was never downloaded.
  $("export-burn").hidden = !(state.doc && state.doc.source === "file"
    && state.doc.media_path && !state.live);
  $("export-dialog").showModal();
}

/* The burn is a background job: an encode of the whole video takes a stretch
 * of real time, so the dialog closes and the job box follows the progress. */
async function submitBurn() {
  const tgt = exportTarget();
  if (!tgt || tgt.value.startsWith("live:")) return;
  const view = document.querySelector('#export-form select[name="view"]').value;
  $("export-dialog").close();
  const res = await (await fetch("/api/burn", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value: tgt.value, view }),
  })).json();
  if (res.error) { jobError(res.error); return; }
  await burnJob(res.id);
}

async function burnJob(id) {
  const box = $("job");
  box.hidden = false; box.classList.remove("error");
  $("job-cancel").disabled = false;
  document.querySelector(".job-label").textContent = t("jobs.burning");
  $("job-label") && ($("job-label").textContent = t("jobs.burning"));
  $("job-source").textContent = "";
  trackJob(id);
  $("job-fill").style.width = "5%";
  $("job-count").textContent = "…";
  while (true) {
    await new Promise(r => setTimeout(r, 700));
    const st = await (await fetch(`/api/job/${id}`)).json();
    const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
    $("job-fill").style.width = Math.max(5, pct) + "%";
    $("job-count").textContent = st.total ? `${st.done}%` : "…";
    if (st.state === "error" || st.state === "interrupted") { jobError(st.error); return; }
    if (st.state === "cancelled") {
      $("job-count").textContent = t("jobs.cancelled");
      setTimeout(() => { box.hidden = true; }, 3000);
      return;
    }
    if (st.state === "done") {
      // The file is beside the source; its name is the answer.
      $("job-count").textContent = st.out ? st.out.split(/[\\/]/).pop() : t("jobs.done.count", { n: 1, secs: st.elapsed || 0 });
      setTimeout(() => { box.hidden = true; }, 6000);
      return;
    }
  }
}

/* Each format has its own catch. Better to read about it before choosing
 * than to find out afterwards. */


function syncExportHint() {
  const f = document.querySelector('#export-form select[name="fmt"]').value;
  const live = !!state.live;
  const key = {
    srt: live ? "export.hint.srt.live" : "export.hint.srt",
    vtt: live ? "export.hint.vtt.live" : "export.hint.vtt",
    txt: "export.hint.txt",
    json: "export.hint.json",
  }[f];
  $("export-hint").textContent = key ? t(key) : "";
}

function submitExport(e) {
  if (e.submitter && e.submitter.value === "cancel") return;
  const tgt = exportTarget();
  if (!tgt) return;
  const f = e.target;
  // Carry the translation engine currently on screen. Left out, the server
  // picks from whatever it has, and that can be a different engine from the
  // one being shown.
  const url = `/api/export?id=${encodeURIComponent(tgt.value)}`
            + `&fmt=${encodeURIComponent(f.fmt.value)}`
            + `&view=${encodeURIComponent(f.view.value)}`
            + `&backend=${encodeURIComponent(state.backend || "")}`;
  // The server attaches Content-Disposition, so simply going there downloads
  // the file. Our server is the same origin and local, so nothing blocks it.
  window.location.href = url;
}
