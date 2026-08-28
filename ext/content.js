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
    // 유튜브의 컨트롤 바보다 아래에 둡니다. 자막이 재생 단추를 덮으면
    // 영상을 조작할 수 없습니다.
    player.appendChild(node);

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
      ov.setData({ cues, backend: prefs.backend || "", live, receiving,
                   speakers: false });
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
      if (c) c.translations[prefs.backend || "_"] = e.text;
    } else if (e.type === "drop") {
      const c = byId.get(e.id);
      if (c) { const i = cues.indexOf(c); if (i >= 0) cues.splice(i, 1); byId.delete(e.id); }
    } else if (e.type === "status") {
      live = true;
      receiving = ["starting", "loading", "running"].includes(e.state);
      // 번역은 그 세션이 쓰는 엔진의 이름으로 들어옵니다.
      if (e.backend) prefs.backend = e.backend;
    }
  }

  function attach(value) {
    cues = []; byId = new Map(); live = false; receiving = false;
    if (!mount()) return;
    if (port) { try { port.disconnect(); } catch (_) {} }
    port = chrome.runtime.connect({ name: "cues" });
    port.onMessage.addListener((m) => {
      if (m.type === "event") onEvent(m.data);
      else if (m.type === "doc") {
        live = false; receiving = false;
        prefs.backend = (m.data.backends_done || []).slice(-1)[0] || "";
        cues = m.data.cues || [];
        byId = new Map(cues.map((c) => [c.id, c]));
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
      reply({ ok: true, mounted: !!node, cues: cues.length, live, receiving });
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

  /* 유튜브는 주소만 갈아 끼우고 페이지를 새로 읽지 않습니다. 영상이 바뀌면
   * 플레이어 요소도 새로 생기므로, 우리가 넣어 둔 것이 사라졌는지 살핍니다. */
  setInterval(() => {
    if (!port) return;                 // 볼 자막이 없으면 아무것도 하지 않습니다
    if (node && node.isConnected) return;
    if (mount()) startTick();
  }, 1500);

  window.addEventListener("resize", () => { if (ov) apply(); });
  document.addEventListener("fullscreenchange", () => { if (ov) apply(); });
})();
