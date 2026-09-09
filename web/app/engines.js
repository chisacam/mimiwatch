/* mimiwatch front end — the engine pickers and the Engines dialog, the genre
 * and content-type pickers, and shutting the server down.
 *
 * One of the files web/app.js was split into by concern. They are all plain
 * <script>s, read in the order index.html writes them down, and they share one
 * global scope -- module syntax is avoided here for the same reason as in
 * overlay.js, which is shared with the extension. Everything they call in each
 * other is a function call made at run time, so the file order only has to put
 * main.js last. */

/* ---------- translation backends ---------- */
async function loadBackends() {
  applyBackends(await (await fetch("/api/backends")).json());
}

/* Seats the config that came back on the screen. At start-up the three
 * requests go out side by side, so fetching and applying have to be two
 * separate things. */
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
  booted = true;      // from here on state agrees with the config, so it is safe to save
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
    // "(not translated)" means "this backend has not run over this file yet", which
    // is a statement about a finished transcript. A live session translates
    // as it goes, so the label would be wrong the moment the first line
    // lands.
    const done = state.doc && (state.doc.backends_done || []).includes(b.id);
    o.textContent = state.doc && !isLiveDoc() && !done
      ? t("engines.picker.untranslated", { label: b.label }) : b.label;
    pick.appendChild(o);
  });
  pick.value = state.backend;
  // The dialog has to point at the same value. The "(not translated)" mark is a
  // statement about the video that is open, so it is not put in the dialog --
  // what is picked there is the engine for a video that does not exist yet.
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

/* Genre is a different axis from the content type. The type decides how many
 * seconds of speech to cut at (live only), the genre decides what vocabulary
 * that speech is carried over into -- which a VOD needs as well. */
function renderGenrePicker() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  if (!sel) return;
  sel.textContent = "";
  state.genres.forEach(g => {
    const o = document.createElement("option");
    o.value = g.id;
    o.textContent = MW_I18N.pick(g, "label");
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
  if (hint && g) hint.textContent = MW_I18N.pick(g, "hint");
}

/* A language change has to relabel the genre options, but renderGenrePicker
 * puts the remembered preference back into the select -- redrawing on its own
 * would silently move the genre of the video that is open. Keep the selection. */
function redrawGenrePicker() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  const keep = sel && sel.value;
  renderGenrePicker();
  if (sel && keep && state.genres.some(g => g.id === keep)) sel.value = keep;
  showGenreHint();
}

function currentGenre() {
  const sel = document.querySelector('#add-form select[name="genre"]');
  return (sel && sel.value) || "general";
}

/* Reflects the genre of the video that is open back into the picker. Without
 * that, the value last picked for some other video stays behind and the wrong
 * prompt goes out on a re-translation. */
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
    o.textContent = t("engines.profile.option",
                      { label: MW_I18N.pick(p, "label"), n: p.max_speech });
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
  loadModels();        // read afresh on every open -- a file may have been put there by hand
  loadCookies();
  loadGlossaries();    // ditto -- a channel list edited in another tab must show
  if (!$("settings-dialog").open) $("settings-dialog").showModal();
}

/* Shuts the server down explicitly.
 *
 * This is not the same as killing the process. The server closes the streams it
 * is receiving first and records them as "ended" before it stops, so the list of
 * past streams tells a shutdown the user asked for apart from a server that
 * died. */
async function shutdownServer() {
  // The server may well be receiving even when this tab is not watching a live
  // stream -- it was started in another tab, or it was already running before
  // this tab was opened. So all that is chosen here is the wording; how many
  // were actually closed comes back in the answer.
  const msg = state.live
    ? t("engines.shutdown.confirm.live")
    : t("engines.shutdown.confirm");
  if (!confirm(msg)) return;

  const btn = $("shutdown");
  const hint = $("shutdown-group").querySelector(".hint");
  btn.disabled = true;
  btn.textContent = t("engines.shutdown.busy");
  $("quit").disabled = true;
  $("quit").textContent = t("engines.shutdown.busy");

  let stopped = null;                     // null = no answer came back
  try {
    const r = await fetch("/api/shutdown", { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    stopped = (await r.json()).sessions_stopped || 0;
  } catch (e) {
    // No answer does not mean it shut down. If the server really stopped, the
    // next request fails too; if it is alive, it answers -- so we ask before we
    // write anything down.
    //
    // This used to write "shut down" right here, which said it had shut down
    // even when the server did not know this route (an older version running,
    // answering 404). Believing that and walking away, the stream goes on being
    // received.
    const alive = await fetch("/api/backends", { cache: "no-store" })
      .then(r => r.ok).catch(() => false);
    if (alive) {
      $("shutdown-group").classList.add("done");
      hint.textContent = t("engines.shutdown.failed", { error: e.message });
      btn.disabled = false;
      btn.textContent = t("engines.shutdown.button");
      $("quit").disabled = false;
      $("quit").textContent = t("engines.quit.button");
      return;
    }
  }

  // Having come this far, the server has stopped. A reload has nowhere to go
  // back to, so the screen is left as it is and only what ended is written down.
  $("shutdown-group").classList.add("done");
  hint.textContent = [
    stopped ? t("engines.shutdown.done.n", { n: stopped }) : t("engines.shutdown.done"),
    t("engines.shutdown.stale"),
    restartHint(),
  ].join(" ");
  btn.textContent = t("engines.shutdown.doneLabel");
  $("quit").textContent = t("engines.shutdown.doneLabel");
  stopLive();
  closeWindows(stopped);
}

/* How to start it again -- one whole sentence per case, because the bundle has
 * no terminal and the repository has no bundle to run. Both the hint and the
 * shutdown screen say it. */
function restartHint() {
  return state.models && state.models.frozen
    ? t("engines.shutdown.restart.bundle") : t("engines.shutdown.restart.repo");
}

/* Closes the window once the server has stopped. For someone running the bundle
 * this tab is all that is left, and stopping only the server while the tab
 * stands there reads as "is it still running?".
 *
 * Chrome lets `window.close()` close even a tab a script did not open **as long
 * as it has a single history entry** -- which is what the tab the bundle opens
 * in the browser is. A tab reached from a bookmark and walked through several
 * pages does not close, and the screen then says so. */
function closeWindows(stopped) {
  if (state.scriptWin && !state.scriptWin.closed) {
    try { state.scriptWin.close(); } catch (_) { /* a different origin cannot be closed */ }
  }
  setTimeout(() => {
    window.close();
    setTimeout(() => {
      if (window.closed) return;
      // Built node by node, not with innerHTML -- the text comes from the
      // string table now, and i18n.js promises a translated line never has a
      // place to smuggle markup into.
      const screen = document.createElement("div");
      screen.className = "quit-screen";
      const h1 = document.createElement("h1");
      const em = document.createElement("em");
      em.textContent = "watch";
      h1.append(document.createTextNode("mimi"), em);
      const said = document.createElement("p");
      said.textContent = stopped
        ? t("engines.shutdown.done.n", { n: stopped }) : t("engines.shutdown.done");
      const how = document.createElement("p");
      how.className = "dim";
      how.append(document.createTextNode(t("engines.quit.manual")),
                 document.createElement("br"),
                 document.createTextNode(restartHint()));
      screen.append(h1, said, how);
      document.body.textContent = "";
      document.body.appendChild(screen);
    }, 400);
  }, 300);
}

function showEngineList() {
  $("engine-form").hidden = true;
  $("model-form").hidden = true;
  $("glossary-form").hidden = true;
  $("settings-body").hidden = false;
  renderEngineList("asr");
  renderEngineList("tr");
}

// The default engines, which cannot be deleted. Gone from the list, there would be nothing left to pick.
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
      ? `${b.base_url} · ${b.model}` : t("engines.row.local");
    name.appendChild(meta);

    const edit = document.createElement("button");
    edit.textContent = t("engines.row.edit");
    edit.disabled = b.backend !== "openai";
    edit.addEventListener("click", () => showEngineForm(kind, b));

    const del = document.createElement("button");
    del.className = "danger";
    del.textContent = t("engines.row.delete");
    if (b.id === LOCKED[kind]) {
      del.title = t("engines.row.lockedTip");
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
  $("form-title").textContent = entry
    ? t(kind === "asr" ? "engines.form.edit.asr" : "engines.form.edit.tr")
    : t(kind === "asr" ? "engines.form.add.asr" : "engines.form.add.tr");
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
  if (!confirm(t("engines.remove.confirm", { name: b.label || b.id }))) return;
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

/* The engine picker sits in two places -- the "Manage ▾" menu and the "Add
 * video" dialog.
 *
 * Transcription starts with that engine the moment a video goes in, so it has
 * to be pickable before it goes in. It used to be pickable in the header only,
 * and with the dialog open there was no way to change it. Both places point at
 * the same value, so picking in either is the same thing. */
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

/* Writes the engine picked under "Manage ▾" down as the server's default as well.
 * The extension starts a session with the server's default, so without this the
 * extension starts on the light engine no matter what was picked on the screen.
 * A failure passes in silence -- the choice on the screen has already changed,
 * and the server may be an older one (no such endpoint). */
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

/* ---------- models and tools ----------
 * The models live outside the program (paths.py). The install script used to
 * fetch them and the screen had nowhere to show what was there -- a missing
 * file ended the session in a FileNotFoundError, leaving nothing but "run
 * ./install.sh". Someone who took the bundle (PyInstaller) does not even have
 * that script, so downloading, deleting and pulling more from Hugging Face all
 * happen here. The server pushes the progress over the bus. */

function fmtBytes(n) {
  if (!n) return "";
  if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + "GB";
  if (n >= 1e6) return Math.round(n / 1e6) + "MB";
  return Math.max(1, Math.round(n / 1e3)) + "KB";
}

async function loadModels() {
  try { applyModels(await (await fetch("/api/models")).json()); }
  catch (_) { /* An older version whose server does not know this endpoint. The section stays empty. */ }
}

function applyModels(ov) {
  state.models = ov;
  const dir = $("model-dir");
  if (dir) dir.textContent = ov.model_dir || "";
  renderModelList();
  refreshSetupNotice();
}

/* The strip up top. It stands whenever even one of the things transcription
 * cannot do without (VAD, whisper, M2M-100, ffmpeg) is absent. While a download
 * runs, its progress is written in the same place. */
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
    text = busy.length > 1
      ? t("engines.setup.downloading.more", { label: cur.label, pct, n: busy.length - 1 })
      : t("engines.setup.downloading", { label: cur.label, pct });
  } else if (busy.length) {
    text = t("engines.setup.queued", { n: busy.length });
  } else {
    const list = missing.map(i => i.label).join(", ");
    text = ov.setup_done ? t("engines.setup.missing", { list })
                         : t("engines.setup.first", { list });
  }
  $("setup-text").textContent = text;
  $("setup-start").hidden = busy.length > 0;
  $("setup-download").hidden = busy.length > 0 || !ov.setup_done;
  box.hidden = false;
}

function stateLabel(it) {
  switch (it.state) {
    case "ready": return t("engines.model.state.ready");
    case "system": return t("engines.model.state.system");
    case "missing": return t("engines.model.state.missing");
    case "partial": return t("engines.model.state.partial", { size: fmtBytes(it.have) });
    case "queued": return t("engines.model.state.queued");
    case "downloading": return it.total
      ? `${Math.round(it.done / it.total * 100)}% · ${fmtBytes(it.done)} / ${fmtBytes(it.total)}`
      : t("engines.model.state.downloaded", { size: fmtBytes(it.done) });
    case "error": return t("engines.model.state.error");
    default: return it.state;
  }
}

function renderModelList() {
  const box = $("model-list");
  const ov = state.models;
  if (!box || !ov) return;
  box.textContent = "";
  // Shown grouped by kind: transcription → translation → auxiliary → tools → files not in the catalog.
  const order = { asr: 0, tr: 1, aux: 2, tool: 3, other: 4 };
  const head = { asr: "engines.model.kind.asr", tr: "engines.model.kind.tr",
                 aux: "engines.model.kind.aux", tool: "engines.model.kind.tool",
                 other: "engines.model.kind.other" };
  const items = [...ov.items].sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9));
  let lastKind = null;
  items.forEach(it => {
    if (it.kind !== lastKind) {
      lastKind = it.kind;
      const h = document.createElement("div");
      h.className = "model-kind";
      h.textContent = head[it.kind] ? t(head[it.kind]) : it.kind;
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
  name.textContent = it.required ? t("engines.model.required", { label: it.label }) : it.label;
  const meta = document.createElement("span");
  meta.className = "meta";
  const bits = [stateLabel(it)];
  if (["ready", "system"].includes(it.state)) bits.push(fmtBytes(it.have || it.size));
  else if (it.size && it.state !== "downloading") bits.push(fmtBytes(it.size));
  const purpose = MW_I18N.pick(it, "purpose");
  if (purpose) bits.push(purpose);
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
    act.textContent = t("engines.model.cancel");
    act.addEventListener("click", () => postModel("/api/models/cancel", { id: it.id }));
  } else if (["missing", "partial", "error", "system"].includes(it.state)) {
    act.textContent = it.state === "partial" ? t("engines.model.resume")
                    : it.state === "error" ? t("engines.model.retry") : t("engines.model.download");
    act.addEventListener("click", () => downloadModels([it.id]));
  } else {
    act.textContent = t("engines.model.download");
    act.disabled = true;
  }
  const del = document.createElement("button");
  del.className = "danger";
  del.textContent = t("engines.row.delete");
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

/* `ids` is either an array or "default" (the default set). With `open` the
 * dialog is opened so the progress can be watched -- which is what "Download
 * for the current settings" in the strip up top does. 5GB takes minutes, and
 * with nothing visible of what is coming it looks stalled. */
async function downloadModels(ids, open = false) {
  const res = await postModel("/api/models/download", { ids });
  if (open && !res.error) openSettings();
}

async function deleteModel(it) {
  const key = it.kind === "tool" ? "engines.model.delete.confirm.tool"
                                 : "engines.model.delete.confirm.model";
  if (!confirm(t(key, { label: it.label, size: fmtBytes(it.have || it.size) }))) return;
  await postModel("/api/models/delete", { id: it.id });
}

/* The new state of one item, as it arrived over the bus. Only that item of the
 * list is swapped in, and whether the required ones are all there is looked at
 * again. An item that is not in the list (a newly added one) rereads the lot. */
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
  // A model whose download finished may have put an entry into the engine config (added from Hugging Face).
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

/* ---------- first-time setup ----------
 * What a machine can do differs from machine to machine. The default is the
 * light CPU pair (SenseVoice Small + M2M-100), but on a machine with a GPU
 * whisper and Gemma are far better. The first run lets them choose, and only
 * the models the chosen pair needs are downloaded. The heavy pair used to be
 * the default, so 6GB came down before anyone learned it was too much for this
 * machine. */

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
    if (o.backend === "openai") bits.push(t("engines.setup.remote"));
    else if (o.model) {
      bits.push(o.model.label);
      bits.push(o.model.state === "ready" ? t("engines.model.state.ready")
                : t("engines.setup.willDownload", { size: fmtBytes(o.model.size) }));
      // M2M-100 uses CTranslate2 pinned to the CPU (translate.py).
      bits.push(o.backend === "local" || o.device === "cpu" ? "CPU" : t("engines.setup.gpuIfAvailable"));
    }
    meta.textContent = bits.join(" · ");
    body.append(name, document.createElement("br"), meta);
    lab.append(r, body);
    box.appendChild(lab);
  });
}

/* How much the chosen pair will newly download. Written beside "Start with
 * this", it says up front whether 5GB is about to come down. */
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
  // M2M-100 is always needed as the translation fallback path. It comes down alongside Gemma too.
  const m2m = (state.models || { items: [] }).items.find(i => i.id === "m2m100");
  if (m2m && m2m.state !== "ready" && !names.includes(m2m.label)) {
    bytes += m2m.size || 0;
    names.push(t("engines.setup.fallbackName", { label: m2m.label }));
  }
  $("setup-total").textContent = bytes
    ? t("engines.setup.total", { list: names.join(", "), size: fmtBytes(bytes) })
    : t("engines.setup.total.none");
}

async function submitSetup(e) {
  if (e.submitter && e.submitter.value === "cancel") return;
  const f = e.target;
  $("viewer-lang").value = f.viewer_lang.value;
  persist();
  saveViewerLang();
  const res = await (await fetch("/api/setup", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asr: f.elements["setup-asr"].value,
                           tr: f.elements["setup-tr"].value, download: true }),
  })).json();
  if (res.error) { alert(res.error); return; }
  // The default engines have changed, so the pickers are brought over to them.
  await loadBackends();
  setAsr(f.elements["setup-asr"].value);
  state.backend = f.elements["setup-tr"].value;
  setBackendPickers(state.backend);
  persist();
  await loadModels();
  if ((res.queued || []).length) openSettings();     // shows the download progress
}

/* ---------- the YouTube login cookies ----------
 * Shows whether the cookies the extension handed over are on the server, and
 * deletes them. The server never gives the contents out -- only present or not,
 * how many, and when they arrived. They are the keys to an account, so deleting
 * them once they have served their purpose is the right thing. */
async function loadCookies() {
  let st;
  try { st = await (await fetch("/api/cookies")).json(); } catch (_) { return; }
  const hint = $("cookies-hint"), del = $("cookies-delete");
  if (!hint) return;
  if (st.present) {
    const when = st.updated ? new Date(st.updated * 1000).toLocaleString() : "";
    hint.textContent = t("engines.cookies.present", { n: st.count || 0, when });
    del.hidden = false;
  } else {
    hint.textContent = st.env ? t("engines.cookies.none.env") : t("engines.cookies.none");
    del.hidden = true;
  }
}

async function deleteCookies() {
  if (!confirm(t("engines.cookies.delete.confirm"))) return;
  await fetch("/api/cookies/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  await loadCookies();
}

/* The pickers, the engine lists and the setup strip are drawn once at startup
 * and then left alone, so a language change has to redraw them by hand -- the
 * static pass only reaches the markup. Each one is guarded: the config may not
 * have arrived yet, and drawing an empty list would empty a filled picker. */
MW_I18N.onChange(() => {
  if ((state.liveProfiles || []).length) renderProfilePicker();
  if ((state.genres || []).length) redrawGenrePicker();
  if ((state.backends || []).length) renderBackendPicker();
  if ((state.backends || []).length && (state.asrBackends || []).length) {
    renderEngineList("asr");
    renderEngineList("tr");
  }
  if (state.models) { renderModelList(); refreshSetupNotice(); }
  renderGlossaryList();
});

/* ---------- channel glossaries ----------
 * A glossary is a list of source → target terms kept per channel, written
 * into the translation prompt (docs/GLOSSARY.md). One line of the textarea
 * is one term; the server stores what the lines say. */
let glossaries = [];

async function loadGlossaries() {
  glossaries = (await fetch("/api/glossaries")).json().catch(() => []) || [];
  renderGlossaryList();
}

function renderGlossaryList() {
  const box = $("glossary-list");
  if (!box) return;
  box.textContent = "";
  if (!glossaries.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = t("settings.glossary.empty");
    box.appendChild(p);
    return;
  }
  glossaries.forEach(g => {
    const row = document.createElement("div");
    row.className = "engine-row";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = g.name || g.channel_key;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `${g.channel_key} · ` +
      t("settings.glossary.row.terms", { n: g.terms.length });
    name.appendChild(meta);
    const edit = document.createElement("button");
    edit.textContent = t("engines.row.edit");
    edit.addEventListener("click", () => showGlossaryForm(g));
    row.append(name, edit);
    box.appendChild(row);
  });
}

/* One line is one term: source → target. The arrow may be typed as ->,
 * because a hardware keyboard does not always hand a → over. */
function parseTerms(text) {
  const out = [];
  text.split(/\r?\n/).forEach(line => {
    const m = line.split(/\s*(?:→|->)\s*/);
    if (m.length === 2 && m[0].trim() && m[1].trim())
      out.push({ from: m[0].trim(), to: m[1].trim() });
  });
  return out;
}

function showGlossaryForm(g) {
  const f = $("glossary-form");
  f.dataset.channelKey = g ? g.channel_key : "";
  $("glossary-form-title").textContent = g
    ? t("settings.glossary.form.edit", { name: g.name || g.channel_key })
    : t("settings.glossary.form.add");
  f.name.value = g ? g.name : "";
  f.key.value = g ? g.channel_key : "";
  f.terms.value = g ? g.terms.map(x => `${x.from} → ${x.to}`).join("\n") : "";
  $("glossary-form-delete").hidden = !g;
  $("glossary-form-error").hidden = true;
  $("settings-body").hidden = true;
  $("engine-form").hidden = true;
  $("model-form").hidden = true;
  f.hidden = false;
}

function backToEngineList() {
  $("glossary-form").hidden = true;
  showEngineList();
}

async function saveGlossaryForm(e) {
  e.preventDefault();
  const f = e.target;
  const name = (f.name.value || "").trim();
  // A name typed where the key is unknown keys as manual:name -- the rule the
  // server applies (store.channel_key), mirrored here so the form answers
  // without a round trip.
  const key = (f.key.value || "").trim() || (name ? "manual:" + name : "");
  if (!key) { jobErrorGlossary(t("settings.glossary.form.name")); return; }
  const res = await (await fetch("/api/glossaries", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel_key: key, name,
                           terms: parseTerms(f.terms.value) }),
  })).json();
  if (res.error) { jobErrorGlossary(res.error); return; }
  await loadGlossaries();
  backToEngineList();
}

function jobErrorGlossary(msg) {
  const p = $("glossary-form-error");
  p.textContent = msg;
  p.hidden = false;
}

async function deleteGlossary(key) {
  if (!key) return;
  await fetch("/api/glossaries", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel_key: key, terms: [] }) });
  await loadGlossaries();
  backToEngineList();
}

$("glossary-add").addEventListener("click", () => showGlossaryForm(null));
$("glossary-form-back").addEventListener("click", backToEngineList);
$("glossary-form-delete").addEventListener("click",
  () => deleteGlossary($("glossary-form").dataset.channelKey));
$("glossary-form").addEventListener("submit", saveGlossaryForm);
