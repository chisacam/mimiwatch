/* The mimiwatch screen — the subtitle log on the right: drawing rows, the
 * editor, picking lines for re-translation, and the popped-out window.
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope -- module
 * syntax is avoided for the same reason as in overlay.js, which is shared with
 * the extension. Everything they call on each other is a function call at run
 * time, so the file order only has to keep main.js last. */

/* ---------- script panel ---------- */
function buildScript() {
  const box = $("script");
  box.textContent = "";
  state.cues.forEach((c, i) => box.appendChild(scriptRow(c, i)));
}

/* Makes one row.
 *
 * Recordings and live streams each used to make their own. With two copies of
 * the code that fills the body, the speaker chip existed on one side only, and
 * adding editing would have made it two copies again. The only difference is
 * how a row is found -- a recording by position (data-i), live by cue number
 * (data-id). Both are attached. */
function scriptRow(c, i) {
  const row = document.createElement("div");
  row.className = "line";
  if (i != null) row.dataset.i = i;
  if (c.id != null) row.dataset.id = c.id;
  const tEl = document.createElement("div");
  tEl.className = "t";
  const body = document.createElement("div");
  row.append(tEl, body);
  refreshScriptRow(row, c);
  row.addEventListener("click", () => {
    // A row being edited does not seek when clicked. If reaching to select some
    // text moves the playhead, there is no way to finish the edit.
    if (row.classList.contains("editing")) return;
    if (state.scriptMode === "edit") { openCueEditor(row, c); return; }
    // No seeking while picking. Picking happens on pointerdown, and calling
    // preventDefault() there still lets the click through -- what that stops is
    // default behaviour such as text selection, not the click event that
    // follows.
    if (state.scriptMode === "tr") return;
    if (state.player) { state.player.seekTo(cueStart(c), true); state.player.playVideo(); }
  });
  // Picking in translation mode. This hangs on pointerdown rather than click
  // because shift+click also starts a text selection -- that has to be stopped
  // first.
  row.addEventListener("pointerdown", (e) => {
    if (state.scriptMode !== "tr") return;
    e.preventDefault();
    pickRow(c.id, e.shiftKey);
  });
  return row;
}

function refreshScriptRow(row, c) {
  row.firstElementChild.textContent = fmt(cueStart(c));
  // A row is built as [time, body, ✎]. Taking the body with lastElementChild
  // wipes out the button from the second call onwards.
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
    // If the source was edited, the translation attached to it is the
    // translation of the **sentence before the edit**. It is not deleted, only
    // marked as such -- a wrong translation still beats none, and whether to
    // translate again is a person's call.
    if (String(c.edited || "").includes("text")) {
      const warn = document.createElement("b");
      warn.className = "tr-stale";
      warn.title = t("panel.row.stale.title");
      warn.textContent = t("panel.row.stale");
      tr.appendChild(warn);
    }
    body.appendChild(tr);
  }
  row.classList.toggle("pending", c.kind === "final" && !trText);
  // The edit button. It is laid over the row and the CSS shows it on hover
  // only. It is attached anew on every redraw -- it closes over c, so leaving
  // the old one behind opens the editor on the value from before the edit
  // rather than on what was edited.
  row.querySelector(":scope > .line-edit")?.remove();
  const pen = document.createElement("button");
  pen.className = "line-edit";
  pen.title = t("panel.row.edit.title");
  pen.textContent = "✎";
  pen.addEventListener("click", (e) => { e.stopPropagation(); openCueEditor(row, c); });
  row.appendChild(pen);
}

/* Switching the language leaves rows already drawn exactly where they are --
 * a recording's subtitle log is drawn once when it is opened and not redrawn
 * until that row is edited, so only the badges and the edit buttons are
 * rewritten in the new language. The count goes with them while lines are being
 * picked -- that one would otherwise stay in the old language until the next
 * click. */
MW_I18N.onChange(() => {
  const box = $("script");
  if (!box) return;
  box.querySelectorAll(".tr-stale").forEach(el => {
    el.title = t("panel.row.stale.title");
    el.textContent = t("panel.row.stale");
  });
  box.querySelectorAll(".line-edit").forEach(el => {
    el.title = t("panel.row.edit.title");
  });
  if (state.scriptMode === "tr") syncPicks();
});

function appendScriptLine(c) {
  const box = $("script");
  const existing = box.querySelector(`.line[data-id="${c.id}"]`);
  if (existing) {
    // A refined line replaces the row under the same id. The character count
    // differs, so the height does too.
    refreshScriptRow(existing, c);
    pinScriptToBottom();
    return;
  }
  box.appendChild(scriptRow(c, null));
  pinScriptToBottom();
}

/* ---------- editing subtitles ----------
 *
 * Transcription gets things wrong. It hears noise as speech, writes proper
 * nouns as something else entirely, and the translation goes wrong once more on
 * top of that. With export attached as well, no way to fix it means it goes out
 * wrong.
 *
 * An edit is saved by the server immediately. Cues now sit one row at a time in
 * the same table whether they came from a recording or from live, so fixing one
 * line never means rewriting that whole video. */
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
  src.placeholder = t("panel.edit.source");
  tr.className = "ce-tr";
  tr.rows = 2;
  tr.placeholder = t("panel.edit.translation");
  tr.value = trOf(c) || "";
  const bar = document.createElement("div");
  bar.className = "ce-bar";
  const at = document.createElement("input");
  at.type = "number"; at.step = "0.1"; at.className = "ce-at";
  at.value = (Math.round(cueStart(c) * 10) / 10).toFixed(1);
  at.title = t("panel.edit.at.title");
  const save = mkbtn(t("panel.edit.save"), "primary-seg");
  const del = mkbtn("🗑", "danger");
  del.title = t("panel.edit.delete.title");
  const cancel = mkbtn(t("panel.edit.cancel"), "");
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
  // Ctrl/⌘+Enter saves. A plain Enter has to be a line break -- one subtitle
  // line is not always one sentence.
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

/* What is being watched, as the value the list uses. The server finds the table by it. */
function editOwner() {
  if (state.live) return "live:" + state.live.id;
  if (state.doc && !isLiveDoc()) return state.doc.id;
  return "";
}

/* Lines up the cue the server returned with what the screen holds. Live keeps it
 * in `t` and a recording in `start`, so the two are reconciled once here. */
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
  resortCue(c);
  state.idx = -1;
  renderCue();
}

/* Moves a line whose time is out of step with its neighbours, both in the array
 * and in its place on screen. On a time edit, on a newly written line, and when
 * a line arrives out of order on a live stream.
 *
 * Cue lookup (cueAt in overlay.js) walks the list assuming it is in time order.
 * One line out of place stops the walk right there, and every lookup after it
 * -- the subtitles on screen and the following -- goes wrong with it. The array
 * is fixed in place (the overlay and the live store hold the same array). An
 * unfocused tile's array (`arr`) is accepted too -- that row is not on screen
 * then, so only the array moves. */
function resortCue(c, arr = state.cues) {
  if (!arr) return;
  const i = arr.indexOf(c);
  if (i < 0) return;
  const misplaced = (i > 0 && cueStart(arr[i - 1]) > cueStart(c))
                 || (i < arr.length - 1 && cueStart(arr[i + 1]) < cueStart(c));
  if (!misplaced) return;
  arr.splice(i, 1);
  let j = arr.findIndex(x => cueStart(x) > cueStart(c));
  if (j < 0) j = arr.length;
  arr.splice(j, 0, c);
  // The row moves to its new place too. Move only the array and the subtitle log's order stays out of step with the times.
  const row = arr === state.cues && c.id != null ? rowOf(c.id) : null;
  if (row) {
    const next = arr[j + 1];
    $("script").insertBefore(row, next && next.id != null ? rowOf(next.id) : null);
  }
}

/* ---------- writing a new line ----------
 *
 * A line that gets deleted is usually a misrecognition -- a sound effect heard
 * as speech, or a place where the line beside it was lost entirely in the
 * process. Editing is only complete once those empty stretches of time can be
 * filled in by hand. The time is pre-filled with the current playhead, and the
 * server makes up the number. */
function openNewCueEditor() {
  const owner = editOwner();
  if (!owner) { alert(t("panel.needTarget")); return; }
  const box = $("script");
  const already = box.querySelector(".line.adding textarea");
  if (already) { already.focus(); return; }            // one at a time
  const t0 = state.player && state.player.ready ? state.player.getCurrentTime() : 0;

  const row = document.createElement("div");
  row.className = "line editing adding";
  const tEl = document.createElement("div");
  tEl.className = "t";
  tEl.textContent = fmt(t0);
  const body = document.createElement("div");
  row.append(tEl, body);

  const ed = document.createElement("div");
  ed.className = "cue-edit";
  const src = document.createElement("textarea");
  src.className = "ce-src";
  src.rows = 2;
  src.placeholder = t("panel.edit.source");
  const tr = document.createElement("textarea");
  tr.className = "ce-tr";
  tr.rows = 2;
  tr.placeholder = t("panel.new.translation");
  const bar = document.createElement("div");
  bar.className = "ce-bar";
  const at = document.createElement("input");
  at.type = "number";
  at.step = "0.1";
  at.className = "ce-at";
  at.value = (Math.round(t0 * 10) / 10).toFixed(1);
  at.title = t("panel.edit.at.title");
  at.addEventListener("input", () => { tEl.textContent = fmt(+at.value || 0); });
  const save = mkbtn(t("panel.edit.save"), "primary-seg");
  const cancel = mkbtn(t("panel.edit.cancel"), "");
  bar.append(at, document.createElement("span"), save, cancel);
  bar.children[1].className = "grow";
  ed.append(src, tr, bar);
  body.appendChild(ed);

  // Inserted at its place in time. Appended at the bottom, a long video means scrolling back up to find it.
  const next = state.cues.find(c => cueStart(c) > t0);
  box.insertBefore(row, next && next.id != null ? rowOf(next.id) : null);
  row.scrollIntoView({ block: "center" });
  src.focus();

  const close = () => row.remove();
  cancel.addEventListener("click", (e) => { e.stopPropagation(); close(); });
  ed.addEventListener("click", (e) => e.stopPropagation());
  ed.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save.click(); }
  });

  save.addEventListener("click", async (e) => {
    e.stopPropagation();
    const text = src.value.trim();
    if (!text) { src.focus(); return; }
    save.disabled = true;
    const res = await (await fetch("/api/cue/add", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: owner, start: +at.value || 0, text,
                             tr: tr.value.trim(), backend: state.backend }),
    })).json();
    if (res.error) { jobError(res.error); save.disabled = false; return; }
    close();
    adoptNewCue(res.cue);
  });
}

/* Seats the new line the server returned with a number of its own into what the
 * screen holds. A live stream still being received also gets it over SSE
 * (cuestore keeps duplicates out by id), but a finished session and a recording
 * have no such channel, so it is put in directly here. */
function adoptNewCue(got) {
  let c;
  if (state.live) {
    c = state.live.store.upsert({ id: got.id, kind: got.kind, t: got.t, end: got.end,
                                  text: got.text, lang: got.lang,
                                  edited: got.edited }).cue;
    c.translations = got.translations || {};
  } else {
    c = { id: got.id, start: got.t, end: got.end, text: got.text, lang: got.lang,
          edited: got.edited, translations: got.translations || {} };
    state.cues.push(c);
  }
  appendScriptLine(c);
  resortCue(c);
  const row = rowOf(c.id);
  if (row) row.scrollIntoView({ block: "center" });
  state.idx = -1;
  renderCue();
}

function dropCue(id, tile = focusedTile()) {
  const live = tile && tile.live;
  if (live) {
    live.store.drop(id);                 // on the focused tile, state.cues is that store's array
  } else {
    const i = state.cues.findIndex(x => x.id === id);
    if (i >= 0) state.cues.splice(i, 1);
  }
  if (tile !== focusedTile()) return;    // another tile's subtitle log is not on screen
  const row = $("script").querySelector(`.line[data-id="${CSS.escape(String(id))}"]`);
  if (row) row.remove();
  state.idx = -1;
  renderCue();
}

/* On a live stream, "Follow" chases the **bottom**, not any particular row.
 *
 * It used to call scrollIntoView({block:"end"}) on each new row. But a row keeps
 * changing height after it is attached -- a translation arrives about 0.2s
 * later and adds a line (refreshScriptRow attaches `.tr`), and when a refined
 * line comes several rows merge into one. Nothing re-aligned at those points, so
 * the bottom row was pushed a little at a time off the screen and looked cut.
 *
 * Pinning the container to the bottom makes it irrelevant how the heights
 * change. Smooth scrolling is not used -- subtitles come in every few hundred
 * milliseconds, so the next animation starts before the previous one ends and
 * it never reaches the bottom at all. */
function pinScriptToBottom() {
  // The bottom is chased only while the stream is being received. A finished
  // broadcast is read like a recording, so keeping the line playing right now in
  // the middle is the right thing (markScript).
  if (!state.follow || !isLiveDoc() || !isLiveReceiving()) return;
  const box = $("script");
  box.scrollTop = box.scrollHeight;
}

function markScript(i) {
  // Nothing is marked while the stream is being received. It is always the last
  // row then, and the script is already pinned to the bottom.
  if (isLiveDoc() && isLiveReceiving()) return;
  const box = $("script");
  box.querySelectorAll(".line.on").forEach(el => el.classList.remove("on"));
  if (i < 0) return;
  // A row is found by cue id, not by position (data-i). The moment one row is
  // deleted, every position after it is off by one -- that is the bug where
  // following kept pointing at the row beside the right one after a line was
  // deleted in the editor. Only rows with no id (the old shape) fall back to
  // position.
  const c = state.cues[i] || {};
  const el = c.id != null ? rowOf(c.id) : box.querySelector(`.line[data-i="${i}"]`);
  if (!el) return;
  el.classList.add("on");
  if (state.follow) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

/* Script mode.
 *
 * Opened as `?script=<key>`, the same app draws nothing but the script. It is
 * for reading the script in this window while watching a broadcast whose
 * embedding is blocked (members-only and the like) on YouTube.
 *
 * There is a reason no separate page was written. Copying SSE, refined-line
 * absorption and the handling of arriving translations would make two places to
 * fix, and that price has already been paid several times over. All that
 * happens here is not building a player and hiding the rest of the shell -- the
 * path that receives and draws subtitles is the one same path. */
function scriptWindowKey() {
  return new URLSearchParams(location.search).get("script") || "";
}

const SCRIPT_WIN = "width=460,height=860,menubar=no,toolbar=no";

function openScriptWindow() {
  const key = state.live ? "live:" + state.live.id
            : (state.doc && !isLiveDoc() ? state.doc.id : "");
  if (!key) { alert(t("panel.needTarget")); return; }
  const url = `/?script=${encodeURIComponent(key)}`;
  // If a window is already up, use it. Starting from tab audio opens one ahead
  // of time below, so opening a fresh one here would leave an empty window and
  // a script window standing apart.
  if (state.scriptWin && !state.scriptWin.closed) {
    state.scriptWin.location = url;
    state.scriptWin.focus();
    return;
  }
  state.scriptWin = window.open(url, "mimiwatch-script", SCRIPT_WIN);
}

/* A window that only holds the place while the session number is still unknown.
 *
 * It is done this way because of ordering. `window.open` only opens right after
 * a user gesture, and tab audio takes several seconds to pick a window to
 * share, by which time the gesture has expired. Trying to open afterwards,
 * Chrome blocks it silently. So an empty window is claimed at the point where
 * the gesture is still alive, and once the session exists that window is turned
 * into the script. */
function openPendingScriptWindow() {
  const win = window.open("", "mimiwatch-script", SCRIPT_WIN);
  if (!win) return null;          // the popup was blocked
  win.document.write(
    '<!doctype html><meta charset="utf-8"><title>' + t("panel.window.title") + '</title>'
    + '<style>html{color-scheme:dark light}'
    + 'body{margin:0;display:grid;place-items:center;height:100vh;'
    + 'font:14px/1.7 system-ui,sans-serif;background:#0e1117;color:#8b95a7;'
    + 'text-align:center;padding:2rem}'
    + '@media(prefers-color-scheme:light){body{background:#fff;color:#666}}'
    + '</style><div>' + t("panel.window.waiting") + '</div>');
  win.document.close();
  return win;
}

/* What happens when a line in the script is clicked.
 *
 * A different axis from "what to show" (setScriptView). That one is which of
 * source and translation to draw; this one is what happens on a click. */
function setScriptMode(m) {
  state.scriptMode = m;
  const box = $("script");
  box.classList.toggle("mode-edit", m === "edit");
  box.classList.toggle("mode-tr", m === "tr");
  $("tr-bar").hidden = m !== "tr";
  $("edit-bar").hidden = m !== "edit";
  if (m !== "tr") clearPicks();
  else markKeptRows();
  document.querySelectorAll("[data-smode]").forEach(b =>
    b.classList.toggle("on", b.dataset.smode === m));
  // Moving out of a mode closes any editor left open. Back in read mode with an
  // editor still standing, that one row plays by different rules than the rest.
  if (m !== "edit") closeAllCueEditors();
}

function closeAllCueEditors() {
  $("script").querySelectorAll(".line.editing .ce-bar button:last-child")
    .forEach(b => b.click());          // each editor's "Cancel"
}

/* ---------- picking the lines to translate again ----------
 *
 * The same rules as a file browser. A click flips that one row, and a
 * shift-click takes everything from the row clicked last up to here in one go.
 * Wanting one passage of a long broadcast run again is no reason to make
 * someone click twenty-three times, one row at a time. */
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

/* Re-translation skips translations a person edited. That is shown before the
 * picking -- pick twelve lines and have two silently drop out, and there is no
 * telling why they did not change. */
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
  $("tr-count").textContent = n === 0 ? t("panel.pick.none")
    : kept ? t("panel.pick.countKept", { n, kept }) : t("panel.pick.count", { n });
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

/* Progress reuses the existing job box as it is. Re-translation runs at 0.15s a
 * line, so even twenty lines take several seconds, and with nothing shown in the
 * meantime it looks stuck. */
async function watchRetranslate(jobId, kept) {
  const box = $("job");
  box.hidden = false;
  box.classList.remove("error");
  $("job-cancel").disabled = false;
  trackJob(jobId);
  while (true) {
    await new Promise(r => setTimeout(r, 500));
    const st = await (await fetch(`/api/job/${jobId}`)).json();
    document.querySelector(".job-label").textContent = t("panel.retranslate.working");
    const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
    $("job-fill").style.width = pct + "%";
    $("job-count").textContent = kept
      ? t("panel.retranslate.progressKept", { done: st.done, total: st.total, kept })
      : t("panel.retranslate.progress", { done: st.done, total: st.total });
    if (st.state === "error") { jobError(st.error); return; }
    if (st.state === "cancelled") { box.hidden = true; state.jobId = null; return; }
    if (st.state === "done") {
      $("job-count").textContent = t(
        kept ? (st.skipped ? "panel.retranslate.doneKeptEmpty" : "panel.retranslate.doneKept")
             : (st.skipped ? "panel.retranslate.doneEmpty" : "panel.retranslate.done"),
        { done: st.done, kept, skipped: st.skipped });
      setTimeout(() => { box.hidden = true; }, 5000);
      state.jobId = null;
      await reloadCues();
      clearPicks();
      return;
    }
  }
}

/* Takes the re-translated lines back onto the screen. Live already had them over
 * SSE, but a recording has no channel to push them down, so they are read once
 * more here. */
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

/* What to show in a script row. A different axis from the on-screen subtitle
 * mode -- that one is over the video, this one is the script itself. */
function setScriptView(v) {
  const box = $("script");
  box.classList.toggle("hide-src", v === "tr");
  box.classList.toggle("hide-tr", v === "src");
  document.querySelectorAll("[data-sview]").forEach(b =>
    b.classList.toggle("on", b.dataset.sview === v));
  const p = loadPrefs(); savePrefs({ ...p, scriptView: v });
  pinScriptToBottom();
}
