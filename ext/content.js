/* 유튜브 페이지 위에 자막을 얹습니다.
 *
 * 우리 페이지의 iframe 위가 아니라 유튜브 페이지 **자체**입니다. 그래서
 * 임베드가 막힌 방송 -- 멤버십 전용이 대부분 그렇습니다 -- 에서도 영상을
 * 그대로 보면서 자막을 읽을 수 있습니다.
 *
 * 얻는 것이 하나 더 있습니다. 여기서는 진짜 <video> 를 잡을 수 있으므로
 * `currentTime` 을 직접 읽습니다. 우리 페이지에서는 임베드된 플레이어의
 * 시계를 못 읽어 라이브 자막 정렬이 수동이었습니다.
 *
 * 자막을 그리는 일은 overlay.js 가 합니다. mimiwatch 페이지와 같은 한
 * 벌입니다 -- 그 파일에 왜 그렇게 뽑았는지 적어 두었습니다.
 */
(function () {
  "use strict";

  const ID = "mimiwatch-overlay";
  // 유튜브는 화면을 갈아 끼우며 돌아다닙니다(SPA). 영상이 바뀔 때마다
  // 플레이어 요소도 새로 생기므로 붙잡아 두지 않고 그때그때 찾습니다.
  const findPlayer = () =>
    document.querySelector("#movie_player") ||
    document.querySelector(".html5-video-player");
  const findVideo = () => document.querySelector("video.html5-main-video") ||
                          document.querySelector("video");

  const log = (...a) => console.log("[mimiwatch]", ...a);

  /* 번역을 어느 이름으로 넣고 어느 이름으로 읽을지.
   *
   * **prefs 에 두면 안 됩니다.** prefs 는 저장했다가 다음에 되읽는데, 그
   * 되읽기가 세션의 status 이벤트보다 늦게 도착하면 옛 엔진 이름이 지금
   * 값을 덮어씁니다. 그러면 넣는 이름과 읽는 이름이 어긋나 번역이 늘
   * 빈 문자열이 되고, 화면에는 **아무것도 나오지 않습니다** -- 「둘 다」에서
   * 원문 줄도 번역이 있을 때만 나오기 때문입니다.
   *
   * 라이브는 한 세션에 번역이 한 벌뿐이므로 이름이 무엇이든 상관없습니다.
   * 붙박이 값을 씁니다. 녹화본만 서버가 준 이름을 그대로 씁니다. */
  const LIVE_KEY = "_";
  let trKey = LIVE_KEY;

  let ov = null;           // overlay 모듈의 조종기
  let node = null;         // 우리가 넣은 div
  let port = null;
  let cues = [];
  let byId = new Map();
  let live = false, receiving = false;
  let tickTimer = null;
  let prefs = { mode: "both", showPrev: true, size: 30, dim: 0.55,
                pos: null, offset: 0 };

  /* ---------- 화면에 자리 만들기 ---------- */

  function mount() {
    const player = findPlayer();
    if (!player) return false;
    if (node && node.isConnected && node.parentElement === player) return true;

    node = document.createElement("div");
    node.id = ID;
    node.className = "mw-overlay";
    node.innerHTML =
      '<div class="mw-prev cue-prev"></div>' +
      '<div class="mw-main cue-main"></div>' +
      '<div class="mw-src cue-src"></div>';
    // 배치는 overlay.css 가 하지만 여기서도 박아 둡니다. 그 파일이 어떤
    // 이유로든 붙지 않으면 자막이 흐름 속의 평범한 블록이 되어 화면 밖으로
    // 밀려나고, 그러면 「아무것도 안 보인다」로만 보입니다.
    node.style.position = "absolute";
    node.style.zIndex = "30";
    node.style.pointerEvents = "none";
    // 유튜브의 컨트롤 바보다 아래에 둡니다. 자막이 재생 단추를 덮으면
    // 영상을 조작할 수 없습니다.
    player.appendChild(node);
    // 우리가 붙는 상자가 자리를 잡고 있어야 absolute 가 그 안에서 셉니다.
    if (getComputedStyle(player).position === "static") {
      player.style.position = "relative";
    }

    ov = MimiOverlay.attach({ overlay: node, box: () => findPlayer() });
    ov.onPos = (p) => { prefs.pos = p; savePrefs(); };
    apply();
    return true;
  }

  function unmount() {
    stopTick();
    if (ov) { ov.destroy(); ov = null; }
    if (node && node.parentElement) node.parentElement.removeChild(node);
    node = null;
  }

  function apply() {
    if (!ov) return;
    ov.setView({ mode: prefs.mode, showPrev: prefs.showPrev });
    // 유튜브의 전체화면은 플레이어 요소가 그대로 커집니다. 그 높이를 기준으로
    // 배율을 잡으면 창에서 고른 크기가 전체화면에서도 같은 비율로 보입니다.
    const p = findPlayer();
    const h = p ? p.clientHeight : 0;
    ov.setSize(prefs.size, h ? Math.max(0.6, h / 480) : 1);
    if (prefs.pos) ov.setPos(prefs.pos);
    node.style.setProperty("--mw-dim", String(prefs.dim));
  }

  /* ---------- 시계 ---------- */

  function startTick() {
    stopTick();
    tickTimer = setInterval(() => {
      if (!ov) return;
      const v = findVideo();
      if (!v) return;
      ov.setData({ cues, backend: trKey, live, receiving, speakers: false });
      ov.render(v.currentTime + (prefs.offset || 0));
    }, 100);
  }

  function stopTick() {
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = null;
  }

  /* ---------- 서버에서 오는 것 ---------- */

  function upsert(m) {
    const old = byId.get(m.id);
    const cue = old || { id: m.id, translations: {} };
    Object.assign(cue, { start: m.t, end: m.end || 0, text: m.text,
                         lang: m.lang, kind: m.kind, speaker: m.speaker || "",
                         arrived: Date.now() });
    if (!old) { cues.push(cue); byId.set(m.id, cue); }
    (m.replaces || []).forEach((id) => {
      if (id === m.id) return;
      const gone = byId.get(id);
      if (!gone) return;
      const i = cues.indexOf(gone);
      if (i >= 0) cues.splice(i, 1);
      byId.delete(id);
    });
  }

  function onEvent(e) {
    if (e.type === "cue") upsert(e);
    else if (e.type === "translation") {
      const c = byId.get(e.id);
      if (c) c.translations[trKey] = e.text;
    } else if (e.type === "drop") {
      const c = byId.get(e.id);
      if (c) { const i = cues.indexOf(c); if (i >= 0) cues.splice(i, 1); byId.delete(e.id); }
    } else if (e.type === "status") {
      live = true;
      receiving = ["starting", "loading", "running"].includes(e.state);

    }
  }

  function attach(value) {
    cues = []; byId = new Map(); live = false; receiving = false;
    trKey = LIVE_KEY;
    if (!mount()) {
      log("플레이어를 찾지 못했습니다. 영상 페이지에서 다시 골라 주십시오.");
      return;
    }
    log("붙었습니다:", value);
    if (port) { try { port.disconnect(); } catch (_) {} }
    port = chrome.runtime.connect({ name: "cues" });
    port.onMessage.addListener((m) => {
      if (m.type === "event") onEvent(m.data);
      else if (m.type === "doc") {
        live = false; receiving = false;
        // 녹화본은 번역이 엔진별로 여러 벌일 수 있습니다. 마지막 것을 씁니다.
        trKey = (m.data.backends_done || []).slice(-1)[0] || LIVE_KEY;
        cues = m.data.cues || [];
        byId = new Map(cues.map((c) => [c.id, c]));
        log(`녹화본 ${cues.length}줄, 번역 열쇠 ${trKey}`);
      } else if (m.type === "error") {
        console.warn("[mimiwatch] " + m.error);
      }
    });
    port.onDisconnect.addListener(() => { port = null; });
    port.postMessage({ type: "attach", value });
    startTick();
  }

  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (msg.type === "attach") { attach(msg.value); reply({ ok: true }); }
    else if (msg.type === "detach") { unmount(); reply({ ok: true }); }
    else if (msg.type === "prefs") {
      Object.assign(prefs, msg.prefs || {});
      savePrefs(); apply(); reply({ ok: true });
    } else if (msg.type === "state") {
      const r = node ? node.getBoundingClientRect() : null;
      reply({ ok: true, mounted: !!(node && node.isConnected),
              cues: cues.length, live, receiving, trKey,
              // 안 보인다는 말은 여러 가지입니다 -- 안 붙었거나, 붙었는데
              // 크기가 0이거나, 그릴 자막이 없거나. 구별할 수 있게 냅니다.
              box: r ? { w: Math.round(r.width), h: Math.round(r.height) } : null,
              text: node ? (node.textContent || "").slice(0, 40) : "",
              player: !!findPlayer(), video: !!findVideo(),
              ticking: !!tickTimer, mode: prefs.mode });
    }
    return true;
  });

  /* ---------- 기억 ---------- */

  const PKEY = "overlayPrefs";
  function savePrefs() { chrome.storage.local.set({ [PKEY]: prefs }); }
  chrome.storage.local.get(PKEY).then((got) => {
    if (got[PKEY]) Object.assign(prefs, got[PKEY]);
    if (ov) apply();
  });

  /* 스스로 물어봅니다.
   *
   * 배경 워커가 `attach` 를 보내지만 그 순간 우리가 없을 수 있습니다 --
   * 확장을 다시 로드한 직후(열려 있던 탭은 옛 content script 를 계속
   * 씁니다), 페이지를 새로고침한 직후, 유튜브가 화면을 갈아 끼운 직후.
   * 그때 배경은 조용히 실패하고 다시 시도하지 않았습니다. 사용자에게는
   * 「골랐는데 안 나온다」로만 보이고, 실제로 페이지를 새로고침해야
   * 나왔습니다.
   *
   * 저장은 이미 되어 있으므로 우리가 읽어 오면 됩니다. */
  function resume() {
    chrome.runtime.sendMessage({ type: "whatToWatch" }, (r) => {
      if (chrome.runtime.lastError || !r || !r.ok || !r.data) return;
      if (port) return;                // 이미 보고 있습니다
      log("이 탭이 보던 것을 이어 붙입니다:", r.data);
      attach(r.data);
    });
  }
  resume();

  /* 유튜브는 주소만 갈아 끼우고 페이지를 새로 읽지 않습니다. 영상이 바뀌면
   * 플레이어 요소도 새로 생기므로, 우리가 넣어 둔 것이 사라졌는지 살핍니다. */
  setInterval(() => {
    if (!port) { resume(); return; }
    if (node && node.isConnected) return;
    if (mount()) startTick();
  }, 1500);

  window.addEventListener("resize", () => { if (ov) apply(); });
  document.addEventListener("fullscreenchange", () => { if (ov) apply(); });
})();
