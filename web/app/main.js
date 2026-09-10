/* mimiwatch front end — wiring the events up (bind) and starting up (init).
 * This one has to be read last.
 *
 * One of the files web/app.js was split into by concern. They are all plain
 * <script>s, read in the order index.html writes them down, and they share one
 * global scope -- module syntax is avoided here for the same reason as in
 * overlay.js, which is shared with the extension. Everything they call in each
 * other is a function call made at run time, so the file order only has to put
 * main.js last. */

// The app language, in two places: the manage menu and the first-time setup
// dialog. It goes to the server, not into this browser's storage -- the machine
// has one language, and the extension reads the same answer from /api/backends.
const UI_LANG_PICKERS = ["ui-lang", "setup-ui-lang"];

MW_I18N.onChange(() => {
  // These two buttons are written on a state change and then left alone, so a
  // language switch that does not reload would leave them in the old language.
  const lib = $("toggle-library"), pan = $("toggle-panel");
  if (lib) lib.textContent = t(state.libraryHidden ? "header.videos.collapsed" : "header.videos");
  if (pan) pan.textContent = t(state.panelHidden ? "header.panel.collapsed" : "header.panel");
});

function bindUiLang() {
  UI_LANG_PICKERS.forEach(id => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("change", async () => {
      const code = el.value;
      MW_I18N.setLang(code);                     // before the round trip, so it feels instant
      UI_LANG_PICKERS.forEach(other => { const s = $(other); if (s) s.value = code; });
      await fetch("/api/uilang", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang: code }),
      }).catch(() => {});
      // Outside first-time setup the switch ends in a reload. Modules that draw
      // once and never subscribe, and the elements applyStatic resets back to a
      // label after JS wrote a title into them, are all correct again after one
      // -- and this is a setting somebody changes once, not a toggle they flip.
      // The setup dialog is the exception: reloading would throw away the
      // choices they are in the middle of making.
      if (id === "ui-lang") location.reload();
    });
  });
}

/* The translation target is remembered on the server (backends.json), so the
 * web page and the extension share one value instead of each holding its own.
 * The browser keeps a local copy too (persist) for a fast paint and an offline
 * fallback, but the server's answer wins on boot -- see init(). A failed write
 * is not worth an error line: the local copy already holds the choice, and the
 * next open asks again. */
function saveViewerLang() {
  fetch("/api/viewerlang", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lang: $("viewer-lang").value }),
  }).catch(() => {});
}

function syncUiLangPickers() {
  UI_LANG_PICKERS.forEach(id => { const s = $(id); if (s) s.value = MW_I18N.current(); });
}

function bind() {
  bindUiLang();
  // Narrowed to `[data-mode]` rather than `.seg`. `.seg` is a shared class that
  // also hangs on buttons with nothing to do with subtitles -- 🗑, ⚙, collapse
  // the script, full screen. Pressing one of those turned state.mode into
  // undefined, which made the source text disappear (only the translation was
  // left), and persist() went on to save that value.
  document.querySelectorAll("[data-mode]").forEach(b => {
    b.addEventListener("click", () => setMode(b.dataset.mode));
  });
  // There are two sets of sliders as well -- the ones under the player and the
  // ones inside the full-screen box. The windowed pair (#size/#dim) owns the
  // value: whichever one is moved, it is gathered there and both are then
  // brought into line.
  for (const name of ["size", "dim"]) {
    $(name).addEventListener("input", e => setControl(name, e.target.value));
    document.querySelectorAll(`[data-ctl="${name}"]`).forEach(el =>
      el.addEventListener("input", e => setControl(name, e.target.value)));
  }
  // The dragging itself is handled by the overlay module, which takes pointer
  // capture on #overlay. The reset button comes in two sets too -- under the
  // player and inside the full-screen box.
  document.querySelectorAll("[data-cue-reset]").forEach(b =>
    b.addEventListener("click", resetCuePos));
  $("show-prev").addEventListener("change", e => { state.showPrev = e.target.checked; persist(); renderCue(); });
  $("follow").addEventListener("change", e => {
    state.follow = e.target.checked;
    pinScriptToBottom();
  });
  $("offset").addEventListener("input", e => {
    state.offset = +e.target.value;
    $("offset-val").textContent = state.offset.toFixed(1) + "s";
    rememberChannelOffset();
    persist();
  });
  $("viewer-lang").addEventListener("change", () => { updateLangStatus(); persist(); saveViewerLang(); });
  $("open-script-window").addEventListener("click", openScriptWindow);
  $("rename-live").addEventListener("click", renameLive);
  $("open-export").addEventListener("click", openExport);
  $("export-form").addEventListener("submit", submitExport);
  document.querySelector('#export-form select[name="fmt"]')
    .addEventListener("change", syncExportHint);
  document.querySelectorAll("[data-sview]").forEach(b =>
    b.addEventListener("click", () => setScriptView(b.dataset.sview)));
  document.querySelectorAll("[data-smode]").forEach(b =>
    b.addEventListener("click", () => setScriptMode(b.dataset.smode)));
  $("tr-all").addEventListener("click", pickAll);
  $("tr-none").addEventListener("click", clearPicks);
  $("tr-go").addEventListener("click", runRetranslate);
  $("cue-add").addEventListener("click", openNewCueEditor);
  $("script-size").addEventListener("input", e => {
    $("script").style.setProperty("--script-size", e.target.value + "px");
    savePrefs({ ...loadPrefs(), scriptSize: +e.target.value });
  });
  $("toggle-panel").addEventListener("click", () => setPanel(!state.panelHidden));
  $("toggle-library").addEventListener("click", () => setLibrary(!state.libraryHidden));
  $("backend-picker").addEventListener("change", e => {
    syncActive("tr", e.target.value);
    selectBackend(e.target.value);
  });
  $("open-manage").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleManage($("manage-menu").hidden);
  });
  // A press outside closes it. A press inside the menu is not a toggle.
  $("manage-menu").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => toggleManage(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleManage(false);
  });
  $("open-settings").addEventListener("click", () => { toggleManage(false); openSettings(); });
  $("settings-close").addEventListener("click", () => $("settings-dialog").close());
  $("shutdown").addEventListener("click", shutdownServer);
  $("quit").addEventListener("click", shutdownServer);
  $("cookies-delete").addEventListener("click", deleteCookies);
  $("form-back").addEventListener("click", showEngineList);
  $("engine-form").addEventListener("submit", saveEngine);
  // Models and tools. A background thread on the server does the downloading and the progress arrives over the bus.
  document.querySelectorAll("[data-add-model]").forEach(b =>
    b.addEventListener("click", showModelForm));
  $("model-form-back").addEventListener("click", showEngineList);
  $("model-form").addEventListener("submit", saveModel);
  $("setup-download").addEventListener("click", () =>
    downloadModels((state.models && state.models.required) || "default", true));
  $("setup-open").addEventListener("click", openSettings);
  $("setup-start").addEventListener("click", openSetup);
  $("setup-again").addEventListener("click", () => { $("settings-dialog").close(); openSetup(); });
  $("setup-form").addEventListener("submit", submitSetup);
  document.querySelectorAll("[data-add]").forEach(b =>
    b.addEventListener("click", () => showEngineForm(b.dataset.add, null)));
  $("asr-picker").addEventListener("change", e => {
    setAsr(e.target.value);
    syncActive("asr", e.target.value);
    if (state.live) askLiveRestart();
  });
  document.querySelector('#add-form select[name="asr"]')
    .addEventListener("change", e => { setAsr(e.target.value); syncActive("asr", e.target.value); });
  document.querySelector('#add-form select[name="backend"]')
    .addEventListener("change", e => { state.backend = e.target.value;
                                       setBackendPickers(state.backend); persist();
                                       syncActive("tr", state.backend); });
  $("job-cancel").addEventListener("click", cancelJob);
  const openAddDialog = (mode) => {
    // Brought up to the current values on every open. An engine deleted, or
    // changed in Engines, has to show in the dialog.
    const f = $("add-form");
    f.dataset.mode = mode || "";
    $("add-dialog").querySelector("h3").textContent =
      mode === "tile" ? t("add.title.tile") : t("add.title");   // put back after a re-transcribe
    renderAsrPicker();
    fillEngineSelect(f.querySelector('select[name="backend"]'), state.backends, state.backend, LOCKED.tr);
    // A tile only ever attaches a live stream taken from a URL. The audio source picker is hidden.
    if (mode === "tile") f.source.value = "url";
    f.source.closest("label").hidden = mode === "tile";
    $("tile-hint").hidden = mode !== "tile";
    setAddSource(f.source.value);
    $("add-dialog").showModal();
  };
  $("add-video").addEventListener("click", () => openAddDialog(""));
  if ($("library-search"))
    $("library-search").addEventListener("input", onLibrarySearchInput);
  if ($("watch-add"))
    $("watch-add").addEventListener("click", addWatcherClick);
  $("export-burn").addEventListener("click", submitBurn);
  $("mv-add").addEventListener("click", () => openAddDialog("tile"));
  document.querySelector('#add-form input[name="refine"]')
    .addEventListener("change", e => { state.refine = e.target.checked; persist(); });
  $("add-form").addEventListener("submit", submitAdd);
  document.querySelector('#add-form select[name="source"]')
    .addEventListener("change", e => setAddSource(e.target.value));
  $("live-stop").addEventListener("click", stopLive);
  // Watching is a full-screen activity; reaching for the mouse to reclaim
  // width breaks it, so the toggle also answers to a key.
  document.addEventListener("keydown", (e) => {
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    if (e.key === "s") setPanel(!state.panelHidden);
    if (e.key === "v") setLibrary(!state.libraryHidden);
    // YouTube's own f shortcut only listens inside the iframe. Once the video
    // has been clicked the focus is in there and nothing reaches this handler,
    // so there is no clash to worry about -- fs:0 holds it off in that case.
    if (e.key === "f") toggleFullscreen();
    // Multiview: the focus follows a number key. With fewer tiles than that, nothing happens.
    if (/^[1-4]$/.test(e.key)) { const t = state.tiles[+e.key - 1]; if (t) setFocus(t); }
  });
  document.querySelectorAll("[data-layout]").forEach(b =>
    b.addEventListener("click", () => applyLayout(b.dataset.layout)));
  document.querySelectorAll("[data-focus-tile]").forEach(b =>
    b.addEventListener("click", () => { const t = state.tiles[+b.dataset.focusTile]; if (t) setFocus(t); }));
  $("fullscreen").addEventListener("click", toggleFullscreen);
  $("fs-exit").addEventListener("click", toggleFullscreen);
  // The controls appear only while the mouse moves and go away shortly after.
  // Standing over the video for good, they get in the way of watching.
  $("player-wrap").addEventListener("mousemove", showFsControls);
  $("fs-controls").addEventListener("mousemove", showFsControls);
  // Movement over the video is swallowed by the iframe and never reaches the
  // handler above. This strip is the only way to call the controls back in
  // full screen.
  $("fs-hotzone").addEventListener("mouseenter", showFsControls);
  $("fs-hotzone").addEventListener("mousemove", showFsControls);
  // A double click over the video cannot be caught. The iframe fills the box
  // and even the letterbox margin belongs to it, so YouTube takes that pair of
  // presses.
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);
  // A window resize in full screen (moving to another display, say) changes the scale too.
  window.addEventListener("resize", () => {
    if (fsElement()) applyCueSize();
    // The cue position is a ratio of the box size, so it has to be measured
    // again in windowed mode as well. The ratio itself does not move, but what
    // counts as running off the edge does.
    applyCuePos();
    if (!$("manage-menu").hidden) toggleManage(true);
  });
}

function setPanel(hidden) {
  state.panelHidden = hidden;
  $("layout").classList.toggle("panel-hidden", hidden);
  $("toggle-panel").textContent = t(hidden ? "header.panel.collapsed" : "header.panel");
  $("toggle-panel").classList.toggle("on", hidden);
  persist();
}

function toggleManage(open) {
  const menu = $("manage-menu"), btn = $("open-manage");
  menu.hidden = !open;
  btn.classList.toggle("on", open);
  btn.setAttribute("aria-expanded", String(!!open));
  if (!open) return;
  // It is fixed, so we place it ourselves: below the button, right edges aligned.
  const r = btn.getBoundingClientRect();
  menu.style.top = `${Math.round(r.bottom + 6)}px`;
  menu.style.left = "auto";
  menu.style.right = `${Math.round(window.innerWidth - r.right)}px`;
}

function setLibrary(hidden) {
  state.libraryHidden = hidden;
  $("layout").classList.toggle("library-hidden", hidden);
  $("toggle-library").textContent = t(hidden ? "header.videos.collapsed" : "header.videos");
  $("toggle-library").classList.toggle("on", hidden);
  persist();
}

(async function init() {
  // The language is settled twice, on purpose. navigator.language is a guess
  // but it lands before the first paint; the machine's own ui_lang is the real
  // answer and it arrives a fetch later. Without the guess the page spends that
  // fetch drawing string keys, which reads as a broken build rather than as a
  // page that has not finished loading.
  MW_I18N.setLang(MW_I18N.fromNavigator());
  initTiles();          // attached first, because restore() puts the cue position in
  restore(); bind();
  const key = scriptWindowKey();
  if (key) {
    state.scriptOnly = true;
    document.body.classList.add("script-only");
  }
  // The three go out side by side. There is no reason for them to wait on each
  // other, and this used to send two of the same ones again further down.
  const [cfg, list, sessions, models] = await Promise.all([
    fetch("/api/backends").then(r => r.json()),
    fetch("/api/videos").then(r => r.json()),
    fetch("/api/live/sessions").then(r => r.json()),
    // With no models a strip stands up top. The page has to appear even if this fails, hence the empty value.
    fetch("/api/models").then(r => r.json()).catch(() => null),
  ]);
  applyBackends(cfg);
  // The machine's choice beats the browser's guess. An empty ui_lang means a
  // config written before this setting existed -- there the guess is all we
  // have, and overwriting it with "" would drag the page back to English.
  if (cfg && cfg.ui_lang) MW_I18N.setLang(cfg.ui_lang);
  syncUiLangPickers();   // both selects show what we actually settled on
  // The remembered translation target settles after the UI language, because
  // the select is drawn in the settled language. The server's answer wins over
  // the browser's local copy, so the last choice made on either surface -- the
  // web page or the extension -- is the default everywhere next time.
  if (cfg && cfg.viewer_lang) $("viewer-lang").value = cfg.viewer_lang;
  if (models && !state.scriptOnly) {
    applyModels(models);
    // First run: the first-time setup has not happened and nothing needed is
    // there. Let them choose. Someone who has been using it all along (no setup
    // mark, but every model present) is not asked.
    if (!models.setup_done && !models.ready) openSetup();
  }
  if (state.scriptOnly) {
    // No list, no player. It opens the one thing it was pointed at.
    await refreshVideoList(undefined, [list, sessions]);
    if ($("video-list").querySelector(`.video-row[data-value="${CSS.escape(key)}"]`)) {
      openFromList(key);
      return;
    }
    // Missing from the list does not mean the session does not exist. The list
    // only carries streams with at least one line transcribed, and a session
    // just started from tab audio still stands at 0 lines when this window
    // opens -- a window that opens by itself would meet "nothing here" every
    // time. Whether the session is real is asked of the server directly.
    if (key.startsWith("live:")) {
      const sid = key.slice(5);
      const st = await (await fetch(`/api/live/status/${encodeURIComponent(sid)}`)).json();
      if (st && st.id) {
        setNowTitle(st.title || "");
        await resumeLive(sid);
        return;
      }
    }
    // A popup URL outlives things -- it gets bookmarked, or reopened still
    // pointing at a video that has been deleted. Having come this far, it
    // really is gone.
    setNowTitle(null);
    const gone = document.createElement("div");
    gone.className = "empty";
    gone.textContent = t("script.gone");
    $("script").replaceChildren(gone);
    return;
  }
  // From here on it is the screen with the list. It takes the changes the
  // server pushes (a new session, a state, a job) and updates without a reload.
  connectBus();
  refreshWatchList();
  if (!list.length && !sessions.some(s => s.cues || LIVE_RUNNING.includes(s.state))) {
    await refreshVideoList(undefined, [list, sessions]);   // the empty-list notice
    setLibrary(false);              // the list is left open for a first-time visitor
    return;
  }
  // The case where the server is fine and only the tab was reloaded. It goes
  // straight back to the stream that was being watched -- there is no way to
  // make those subtitles a second time.
  const running = sessions.find(s => LIVE_RUNNING.includes(s.state));
  await refreshVideoList(running ? null : (list[0] || {}).id, [list, sessions]);
  if (running) {
    // If it is a member of a multiview bundle, the whole bundle comes back. If
    // the bundle is gone (a server restart), that one session opens alone.
    if (running.group && await openMultiview(running.group)) return;
    markVideoRow("live:" + running.id);
    await resumeLive(running.id);
  }
})();
