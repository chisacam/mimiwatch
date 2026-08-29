/* mimiwatch 화면 — 이벤트 연결(bind)과 시동(init). 마지막에 읽혀야 합니다.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

function bind() {
  // `.seg`가 아니라 `[data-mode]`로 좁힙니다. `.seg`는 🗑, ⚙, 스크립트 접기,
  // 전체화면처럼 자막과 무관한 단추도 달고 있는 공용 클래스입니다. 그것들을
  // 누르면 state.mode가 undefined가 되어 원문이 사라졌고(번역만 남습니다),
  // persist()가 그 값을 저장까지 했습니다.
  document.querySelectorAll("[data-mode]").forEach(b => {
    b.addEventListener("click", () => setMode(b.dataset.mode));
  });
  // 슬라이더도 두 벌입니다 -- 플레이어 아래의 것과 전체화면 상자 안의 것.
  // 창 쪽(#size/#dim)을 값의 주인으로 두고, 어느 쪽을 움직이든 그리로
  // 모은 뒤 양쪽 표시를 맞춥니다.
  for (const name of ["size", "dim"]) {
    $(name).addEventListener("input", e => setControl(name, e.target.value));
    document.querySelectorAll(`[data-ctl="${name}"]`).forEach(el =>
      el.addEventListener("input", e => setControl(name, e.target.value)));
  }
  // 끌기 자체는 overlay 모듈이 #overlay 에 포인터 캡처를 걸어 처리합니다.
  // 되돌리기 단추도 두 벌입니다 -- 플레이어 아래와 전체화면 상자 안.
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
    persist();
  });
  $("viewer-lang").addEventListener("change", () => { updateLangStatus(); persist(); });
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
  // 바깥을 누르면 닫습니다. 메뉴 안을 누르는 것은 여닫기가 아닙니다.
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
  // 모델·도구. 내려받기는 서버의 배경 스레드가 하고 진행은 bus 로 옵니다.
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
    // 열 때마다 지금 값으로 맞춥니다. 엔진을 지웠거나 「관리」에서 바꾼
    // 것이 대화상자에 반영되어 있어야 합니다.
    const f = $("add-form");
    f.dataset.mode = mode || "";
    $("add-dialog").querySelector("h3").textContent =
      mode === "tile" ? "타일 추가 · 멀티뷰" : "영상 추가";   // 다시 전사 뒤에 되돌립니다
    renderAsrPicker();
    fillEngineSelect(f.querySelector('select[name="backend"]'), state.backends, state.backend, LOCKED.tr);
    // 타일은 주소로 받는 라이브만 붙입니다. 소리 출처 고르기는 숨깁니다.
    if (mode === "tile") f.source.value = "url";
    f.source.closest("label").hidden = mode === "tile";
    $("tile-hint").hidden = mode !== "tile";
    setAddSource(f.source.value);
    $("add-dialog").showModal();
  };
  $("add-video").addEventListener("click", () => openAddDialog(""));
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
    // 유튜브의 f 단축키는 iframe 안에서만 듣습니다. 영상을 클릭한 뒤에는
    // 초점이 그 안에 있어 이 처리기까지 오지 않으므로, 겹칠 걱정은
    // 없습니다 -- 대신 그때는 fs:0 이 막아 줍니다.
    if (e.key === "f") toggleFullscreen();
    // 멀티뷰: 숫자 키로 초점. 타일이 그만큼 없으면 아무 일도 없습니다.
    if (/^[1-4]$/.test(e.key)) { const t = state.tiles[+e.key - 1]; if (t) setFocus(t); }
  });
  document.querySelectorAll("[data-layout]").forEach(b =>
    b.addEventListener("click", () => applyLayout(b.dataset.layout)));
  document.querySelectorAll("[data-focus-tile]").forEach(b =>
    b.addEventListener("click", () => { const t = state.tiles[+b.dataset.focusTile]; if (t) setFocus(t); }));
  $("fullscreen").addEventListener("click", toggleFullscreen);
  $("fs-exit").addEventListener("click", toggleFullscreen);
  // 조절기는 마우스가 움직일 때만 뜨고 잠시 뒤 사라집니다. 영상 위에 계속
  // 떠 있으면 보는 것을 방해합니다.
  $("player-wrap").addEventListener("mousemove", showFsControls);
  $("fs-controls").addEventListener("mousemove", showFsControls);
  // 영상 위의 움직임은 iframe이 삼키므로 위의 처리기까지 오지 않습니다.
  // 이 띠만이 전체화면에서 조절기를 다시 부르는 길입니다.
  $("fs-hotzone").addEventListener("mouseenter", showFsControls);
  $("fs-hotzone").addEventListener("mousemove", showFsControls);
  // 영상 위 더블클릭은 받을 수 없습니다. iframe이 상자를 꽉 채우고 있어
  // letterbox 여백까지 iframe의 것이라, 그 두 번 누름은 유튜브가 가져갑니다.
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);
  // 전체화면에서 창 크기가 바뀌면(다른 화면으로 옮기는 등) 배율도 바뀝니다.
  window.addEventListener("resize", () => {
    if (fsElement()) applyCueSize();
    // 자막 자리는 상자 크기에 대한 비율이라 창 모드에서도 다시 재야 합니다.
    // 비율 자체는 그대로지만, 밖으로 나가는지 자르는 기준이 달라집니다.
    applyCuePos();
    if (!$("manage-menu").hidden) toggleManage(true);
  });
}

function setPanel(hidden) {
  state.panelHidden = hidden;
  $("layout").classList.toggle("panel-hidden", hidden);
  $("toggle-panel").textContent = hidden ? "스크립트 ◂" : "스크립트 ▸";
  $("toggle-panel").classList.toggle("on", hidden);
  persist();
}

function toggleManage(open) {
  const menu = $("manage-menu"), btn = $("open-manage");
  menu.hidden = !open;
  btn.classList.toggle("on", open);
  btn.setAttribute("aria-expanded", String(!!open));
  if (!open) return;
  // fixed 라 자리를 직접 잡아 줍니다. 단추 아래, 오른쪽 끝을 맞춥니다.
  const r = btn.getBoundingClientRect();
  menu.style.top = `${Math.round(r.bottom + 6)}px`;
  menu.style.left = "auto";
  menu.style.right = `${Math.round(window.innerWidth - r.right)}px`;
}

function setLibrary(hidden) {
  state.libraryHidden = hidden;
  $("layout").classList.toggle("library-hidden", hidden);
  $("toggle-library").textContent = hidden ? "▸ 영상" : "◧ 영상";
  $("toggle-library").classList.toggle("on", hidden);
  persist();
}

(async function init() {
  initTiles();          // restore() 가 자막 자리를 넣으므로 먼저 붙입니다
  restore(); bind();
  const key = scriptWindowKey();
  if (key) {
    state.scriptOnly = true;
    document.body.classList.add("script-only");
  }
  // 셋을 나란히 보냅니다. 서로 기다릴 이유가 없고, 예전에는 이 뒤에서
  // 같은 둘을 한 번 더 보냈습니다.
  const [cfg, list, sessions, models] = await Promise.all([
    fetch("/api/backends").then(r => r.json()),
    fetch("/api/videos").then(r => r.json()),
    fetch("/api/live/sessions").then(r => r.json()),
    // 모델이 없으면 위쪽에 띠를 세웁니다. 실패해도 화면은 떠야 하므로 빈 값으로.
    fetch("/api/models").then(r => r.json()).catch(() => null),
  ]);
  applyBackends(cfg);
  if (models && !state.scriptOnly) {
    applyModels(models);
    // 첫 실행: 초기 설정을 아직 안 했고 필요한 것도 없습니다. 고르게 합니다.
    // 예전부터 쓰던 사람(설정 표시는 없지만 모델은 다 있음)에게는 묻지 않습니다.
    if (!models.setup_done && !models.ready) openSetup();
  }
  if (state.scriptOnly) {
    // 목록도 플레이어도 없습니다. 지목된 것 하나만 엽니다.
    await refreshVideoList(undefined, [list, sessions]);
    if ($("video-list").querySelector(`.video-row[data-value="${CSS.escape(key)}"]`)) {
      openFromList(key);
      return;
    }
    // 목록에 없다고 없는 세션은 아닙니다. 목록은 한 줄이라도 받아 적은
    // 방송만 올리는데, 탭 소리로 막 시작한 세션은 이 창이 열리는 시점에
    // 아직 0줄입니다 -- 자동으로 띄우는 창이 매번 「없습니다」를 보게 됩니다.
    // 세션이 실재하는지는 서버에 직접 묻습니다.
    if (key.startsWith("live:")) {
      const sid = key.slice(5);
      const st = await (await fetch(`/api/live/status/${encodeURIComponent(sid)}`)).json();
      if (st && st.id) {
        setNowTitle(st.title || "");
        await resumeLive(sid);
        return;
      }
    }
    // 팝업 주소는 살아남습니다 -- 즐겨찾기에 들어가거나, 지운 영상을
    // 가리킨 채 다시 열립니다. 여기까지 왔으면 정말로 없는 것입니다.
    setNowTitle(null);
    $("script").innerHTML =
      '<div class="empty">이 자막 내역은 더 이상 없습니다. 본 창에서 다시 여십시오.</div>';
    return;
  }
  // 여기부터는 목록이 있는 화면입니다. 서버가 밀어 주는 변화(새 세션·상태·
  // 작업)를 받아 새로고침 없이 갱신합니다.
  connectBus();
  if (!list.length && !sessions.some(s => s.cues || LIVE_RUNNING.includes(s.state))) {
    await refreshVideoList(undefined, [list, sessions]);   // 빈 목록 안내
    setLibrary(false);              // 처음 온 사람에게는 목록을 펼쳐 둡니다
    return;
  }
  // 서버는 멀쩡한데 탭만 새로고침한 경우입니다. 보고 있던 방송으로 그대로
  // 돌아갑니다 -- 그 자막을 다시 만들 방법은 없으니까요.
  const running = sessions.find(s => LIVE_RUNNING.includes(s.state));
  await refreshVideoList(running ? null : (list[0] || {}).id, [list, sessions]);
  if (running) {
    // 멀티뷰 묶음의 멤버면 묶음을 통째로 되살립니다. 묶음이 없어졌으면(서버 재시작)
    // 그 세션 하나만 엽니다.
    if (running.group && await openMultiview(running.group)) return;
    markVideoRow("live:" + running.id);
    await resumeLive(running.id);
  }
})();
