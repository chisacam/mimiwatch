/* Lays subtitles over the YouTube page.
 *
 * The YouTube page **itself**, not over our page's iframe. So a stream where
 * embedding is blocked -- which most members-only ones are -- can be watched as
 * it is with the subtitles read on top.
 *
 * There is one more thing gained. Here the real <video> can be grabbed, so
 * `currentTime` is read directly. On our page the embedded player's clock
 * cannot be read, which left live subtitle alignment a manual affair.
 *
 * Drawing the subtitles is overlay.js's job. It is the same copy the mimiwatch
 * page uses -- that file says why it was pulled out that way.
 */
(function () {
  "use strict";

  const ID = "mimiwatch-overlay";
  // YouTube swaps the screen out as it navigates (an SPA). The player element
  // is created afresh on every video change, so it is not held on to but looked
  // up each time.
  const findPlayer = () =>
    document.querySelector("#movie_player") ||
    document.querySelector(".html5-video-player");
  const findVideo = () => document.querySelector("video.html5-main-video") ||
                          document.querySelector("video");

  const log = (...a) => console.log("[mimiwatch]", ...a);

  /* Which name a translation goes in under and comes out under.
   *
   * **It must not sit in prefs.** prefs is stored and read back next time, and
   * if that read-back arrives later than the session's status event, the old
   * engine name overwrites the current value. The name it goes in under and the
   * name it comes out under then disagree, the translation is always the empty
   * string, and **nothing at all appears** on screen -- because in "Both" the
   * source line too only shows when there is a translation.
   *
   * Live has one set of translations per session, so the name does not matter.
   * It uses a fixed value. Only a VOD uses the name the server gave. */
  const LIVE_KEY = "_";
  let trKey = LIVE_KEY;

  /* Which video these subtitles belong to. The empty string means it could not
   * be worked out -- then it asks rather than taking them down. */
  let expectVideo = "";
  let currentValue = "";        // what is laid on now. Used to reattach when the server address changes.
  const videoIdOf = MimiYtId.videoIdOf;      // the same copy the service worker uses (ytid.js)

  let ov = null;           // the overlay module's handle
  let node = null;         // the div we put in
  let port = null;
  // The subtitle list. The rules for folding events in (swap under the same id,
  // take replaces out) belong to cuestore.js, shared with the mimiwatch page.
  // The array is edited in place, so `cues` is grabbed once and used as it is.
  const store = MimiCues.create();
  const cues = store.cues;
  let live = false, receiving = false;
  // Has the line to the server broken. True while the service worker is trying
  // to reattach. The popup's status line tells "no subtitle in the current
  // segment" from "server connection lost".
  let stalled = false;
  let tickTimer = null;
  let prefs = { mode: "both", showPrev: true, size: 30, dim: 0.55,
                pos: null, offset: 0, panel: false };

  /* ---------- making room on screen ---------- */

  function mount() {
    const player = findPlayer();
    if (!player) return false;
    if (node && node.isConnected && node.parentElement === player) return true;
    // Sweep up everything left. Tracking only one while YouTube swaps the
    // player out loses the old one, and then the subtitles show in two layers.
    document.querySelectorAll("#" + ID).forEach((e) => e.remove());

    node = document.createElement("div");
    node.id = ID;
    node.className = "mw-overlay";
    // It does not use innerHTML. YouTube has Trusted Types switched on
    // (`require-trusted-types-for 'script'`), and putting a string into
    // innerHTML on that document is refused. Whether a content script is exempt
    // depends on the Chrome version, so it does not lean on it at all.
    for (const [a, b] of [["mw-prev", "cue-prev"], ["mw-main", "cue-main"],
                          ["mw-src", "cue-src"]]) {
      const d = document.createElement("div");
      d.className = a + " " + b;
      node.appendChild(d);
    }
    // Layout is overlay.css's job, but it is nailed down here as well. If that
    // file fails to attach for any reason the subtitle becomes an ordinary block
    // in the flow and is pushed off screen, and that only ever looks like
    // "nothing shows".
    node.style.position = "absolute";
    node.style.zIndex = "30";
    node.style.pointerEvents = "none";
    // Below YouTube's control bar. A subtitle covering the play button makes
    // the video impossible to operate.
    player.appendChild(node);
    // The box we attach to has to be positioned for absolute to count inside it.
    if (getComputedStyle(player).position === "static") {
      player.style.position = "relative";
    }

    ov = MimiOverlay.attach({ overlay: node, box: () => findPlayer() });
    ov.onPos = (p) => { prefs.pos = p; savePrefs(); };
    apply();
    return true;
  }

  /* Takes it down on this tab. Erasing it from the screen is not enough.
   *
   * **The port has to be disconnected.** It used not to be, so the watcher that
   * runs every 1.5 s saw `port` alive, decided the overlay had disappeared and
   * put it back up. Press the button and it vanished for a moment, then came
   * back with the next subtitle. */
  function unmount() {
    stopTick();
    if (port) { try { port.disconnect(); } catch (_) {} port = null; }
    MimiPanel.unmount();
    MimiPanel.reset();
    if (ov) { ov.destroy(); ov = null; }
    document.querySelectorAll("#" + ID).forEach((e) => e.remove());
    node = null;
    store.reset();
  }

  /* The subtitle log in the chat column. Switched on and off separately from
   * the subtitles over the video -- the overlay is the one line right now, and
   * this is the place to look back over what went past. */
  function syncPanel() {
    // It is not put up unless we are attached. This function runs after taking
    // it down too, and looking only at `prefs.panel` has the subtitle log come
    // back up by itself.
    if (prefs.panel && port) {
      if (!MimiPanel.mounted()) {
        MimiPanel.reset();
        if (!MimiPanel.mount()) return;
        MimiPanel.setSeek((t) => {
          const v = findVideo();
          if (v) { v.currentTime = t; v.play().catch(() => {}); }
        });
        log("put the subtitle log where the chat was");
      }
      MimiPanel.render(cues, { trKey });
    } else if (MimiPanel.mounted()) {
      MimiPanel.unmount();
    }
  }

  function apply() {
    // The subtitle log is separate from the overlay. This line used to sit after
    // the `if (!ov) return` below, so with the overlay not yet (or no longer)
    // there, switching the checkbox off did not make the log go away.
    syncPanel();
    if (!ov) return;
    ov.setView({ mode: prefs.mode, showPrev: prefs.showPrev });
    // YouTube's full screen grows the player element itself. Scaling against
    // that height makes a size chosen in the window look the same proportion in
    // full screen.
    const p = findPlayer();
    const h = p ? p.clientHeight : 0;
    ov.setSize(prefs.size, h ? Math.max(0.6, h / 480) : 1);
    if (prefs.pos) ov.setPos(prefs.pos);
    node.style.setProperty("--mw-dim", String(prefs.dim));
  }

  /* ---------- the clock ---------- */

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

  /* ---------- what comes from the server ---------- */

  /* The subtitle log is redrawn only when the subtitles change. Put on the
   * render loop (100ms) it would sweep hundreds of lines ten times a second,
   * and the log only changes when a new line arrives. */
  let panelDirty = false;
  setInterval(() => {
    if (!panelDirty) return;
    panelDirty = false;
    if (prefs.panel) syncPanel();
  }, 400);

  function onEvent(e) {
    panelDirty = true;
    if (e.type === "cue") store.upsert(e);
    else if (e.type === "translation") store.translate(e.id, trKey, e.text);
    else if (e.type === "drop") store.drop(e.id);
    else if (e.type === "status") {
      live = true;
      receiving = ["starting", "loading", "running"].includes(e.state);

    }
  }

  function attach(value, videoId) {
    currentValue = value;
    store.reset(); live = false; receiving = false; stalled = false;
    trKey = LIVE_KEY;
    expectVideo = videoId || "";
    dismissAsk();
    MimiPanel.reset();
    if (!mount()) {
      log("no player found. Pick it again on a video page.");
      return;
    }
    log("attached:", value);
    if (port) { try { port.disconnect(); } catch (_) {} }
    port = chrome.runtime.connect({ name: "cues" });
    port.onMessage.addListener((m) => {
      if (m.type === "event") { stalled = false; onEvent(m.data); }
      else if (m.type === "stalled") stalled = true;
      else if (m.type === "ended") {
        // A finished session has the server send the whole backlog and close.
        // Nothing more is coming, so it moves from the "receiving" rule (hold
        // the most recent line) to the time-based lookup.
        stalled = false; receiving = false;
      }
      else if (m.type === "doc") {
        live = false; receiving = false;
        // A VOD may have several sets of translations, one per engine. It uses
        // the last of them.
        trKey = (m.data.backends_done || []).slice(-1)[0] || LIVE_KEY;
        store.load(m.data.cues || []);
        log(`VOD, ${cues.length} lines, translation key ${trKey}`);
        MimiPanel.reset();
        panelDirty = true;
      } else if (m.type === "error") {
        console.warn("[mimiwatch] " + m.error);
      }
    });
    port.onDisconnect.addListener(() => {
      port = null;
      // Chrome cuts extension ports about every 5 minutes (the service worker
      // lifetime rule). If we are receiving it reattaches -- otherwise the
      // subtitles stop quietly and "server connection lost" does not even show.
      if (currentValue === value && receiving !== false) {
        setTimeout(() => { if (!port && currentValue === value) attach(value, videoId); }, 1000);
      }
    });
    port.postMessage({ type: "attach", value });
    startTick();
    // Called once more after the port is up. The apply() inside mount() runs
    // ahead of this line, and at that point there is no port yet, so the
    // subtitle log does not go up.
    syncPanel();
  }

  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (msg.type === "attach") { attach(msg.value, msg.videoId); reply({ ok: true }); }
    else if (msg.type === "reattach") {
      // The server address has changed. What was attached is reattached on the
      // new address.
      if (port && currentValue) attach(currentValue, expectVideo);
      reply({ ok: true });
    }
    else if (msg.type === "detach") { unmount(); reply({ ok: true }); }
    else if (msg.type === "prefs") {
      Object.assign(prefs, msg.prefs || {});
      savePrefs(); apply(); reply({ ok: true });
    } else if (msg.type === "state") {
      const r = node ? node.getBoundingClientRect() : null;
      reply({ ok: true, mounted: !!(node && node.isConnected),
              cues: cues.length, live, receiving, stalled, trKey,
              // "It does not show" means several things -- not attached, or
              // attached at size 0, or nothing to draw. They are reported apart
              // so they can be told from each other.
              box: r ? { w: Math.round(r.width), h: Math.round(r.height) } : null,
              text: node ? (node.textContent || "").slice(0, 40) : "",
              player: !!findPlayer(), video: !!findVideo(),
              ticking: !!tickTimer, mode: prefs.mode,
              panel: prefs.panel, panelUp: MimiPanel.mounted() });
    }
    return true;
  });

  /* ---------- memory ---------- */

  const PKEY = "overlayPrefs";
  function savePrefs() { chrome.storage.local.set({ [PKEY]: prefs }); }
  chrome.storage.local.get(PKEY).then((got) => {
    if (got[PKEY]) Object.assign(prefs, got[PKEY]);
    if (ov) apply();
  });

  /* The UI language. The server holds that value, but it is not asked for here
   * -- this script goes out with the YouTube page's origin so it does not reach
   * the server directly, and reaching the server is the service worker's job.
   * It reads the value the popup received from the server and wrote down, and
   * follows it when it changes. If nobody has asked yet (a browser where the
   * popup was never opened) it goes with the browser's guess. */
  const LANG_KEY = "uiLang";
  chrome.storage.local.get(LANG_KEY)
    .then((got) => MW_I18N.setLang(got[LANG_KEY] || ""));

  /* It asks on its own.
   *
   * The service worker sends `attach`, but we may not be there at that moment
   * -- right after reloading the extension (tabs already open keep using the old
   * content script), right after a page reload, right after YouTube swapped the
   * screen out. The background then failed quietly and never tried again. To the
   * user that only looked like "I picked it and nothing shows", and the page
   * really had to be reloaded before it did.
   *
   * The choice is already stored, so we only have to read it. */
  /* YouTube swaps only the URL out (an SPA). Move to a different video and the
   * player and the right-hand column are both created afresh, while what we put
   * in stayed behind still holding the old video's subtitles. On a URL change it
   * is swept away once and put back up. */
  let lastUrl = location.href;
  function onNavigate() {
    if (location.href === lastUrl) return;
    const wasVideo = videoIdOf(lastUrl);
    lastUrl = location.href;
    const now = videoIdOf(lastUrl);
    if (!port) {
      // Nothing is laid on. Still, this tab may have something set to watch
      // that could not attach earlier because it was not a video page, so it
      // asks once.
      if (now) resume();
      return;
    }
    if (now && now === wasVideo) return;     // a move inside the same video (a seek and the like)

    if (expectVideo && now && now !== expectVideo) {
      // Told for certain. A different video, so it comes down. These subtitles
      // are not that video's, and left up they stick the wrong words on screen.
      log(`a different video (${expectVideo} → ${now}). Taking it down.`);
      unmount();
      chrome.runtime.sendMessage({ type: "dropWatch" });
      note(t("content.noteTakenDown"));
      return;
    }
    if (!expectVideo && now) {
      // Which video the subtitles belong to could not be worked out (started
      // from the tab's sound with an unreadable URL, and the like). It asks
      // rather than taking them down on its own.
      ask();
      return;
    }
    // The same video. The screen may have been swapped out, so it is put back up.
    log("the page was swapped out. Putting it back up.");
    MimiPanel.unmount();
    MimiPanel.reset();
    if (node) { node.remove(); node = null; }
    if (ov) { ov.destroy(); ov = null; }
    stopTick();
    if (mount()) { startTick(); apply(); }
  }
  // YouTube fires this event on the document every time it swaps the screen out.
  // It used to look at the URL every 0.7 s, which was a clock running forever on
  // every YouTube tab. One slow clock is left for versions where the event does
  // not arrive (or for after it is renamed).
  document.addEventListener("yt-navigate-finish", onNavigate);
  setInterval(onNavigate, 3000);

  /* ---------- asking ----------
   *
   * It does not use confirm(). It stops the page dead, and over a video it hangs
   * playback with it. A small bar is placed inside the player instead. */
  const ASK_ID = "mimiwatch-ask";

  function dismissAsk() {
    document.querySelectorAll("#" + ASK_ID).forEach((e) => e.remove());
  }

  function note(text) {
    const bar = putAsk(text);
    if (bar) setTimeout(() => bar.remove(), 6000);
  }

  function putAsk(text) {
    const player = findPlayer();
    if (!player) return null;
    dismissAsk();
    const bar = document.createElement("div");
    bar.id = ASK_ID;
    bar.className = "mw-ask";
    const span = document.createElement("span");
    span.textContent = text;
    bar.appendChild(span);
    player.appendChild(bar);
    return bar;
  }

  function ask() {
    const bar = putAsk(t("content.askMoved"));
    if (!bar) return;
    const keep = document.createElement("button");
    keep.textContent = t("content.askKeep");
    keep.addEventListener("click", () => {
      // The user answered that this is that video. So it is not asked again,
      // the current video is written down as this subtitle's.
      expectVideo = videoIdOf(location.href);
      dismissAsk();
      MimiPanel.reset();
      if (node) { node.remove(); node = null; }
      if (ov) { ov.destroy(); ov = null; }
      stopTick();
      if (mount()) { startTick(); apply(); }
    });
    const drop = document.createElement("button");
    drop.textContent = t("content.askTakeDown");
    drop.addEventListener("click", () => {
      dismissAsk();
      unmount();
      chrome.runtime.sendMessage({ type: "dropWatch" });
    });
    bar.append(keep, drop);
  }

  function resume() {
    chrome.runtime.sendMessage({ type: "whatToWatch" }, (r) => {
      if (chrome.runtime.lastError || !r || !r.ok || !r.data) return;
      if (port) return;                // already watching
      log("reattaching what this tab was watching:", r.data);
      attach(r.data, r.videoId);
    });
  }
  resume();

  /* When the popup sets what this tab watches, the storage (`tab:<number>`)
   * changes first. That change is heard here -- it used to ask the service
   * worker whether there was anything to watch every 1.5 s while nothing was
   * attached, which left the worker no gap to sleep in as long as a YouTube tab
   * was open. Which tab's key it is we do not know, but asking is cheap, and if
   * it is not our tab the background gives an empty answer. */
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    // The popup received a new language from the server and wrote it down. The
    // heading of a subtitle log already up is rewritten by panel.js itself.
    if (changes[LANG_KEY]) MW_I18N.setLang(changes[LANG_KEY].newValue || "");
    if (!port && Object.keys(changes).some((k) => k.startsWith("tab:"))) resume();
  });

  /* YouTube swaps only the URL out and does not read the page anew. The player
   * element is created afresh when the video changes, so this watches for what
   * we put in having disappeared. A clock that only looks at the document, so it
   * does not wake the service worker. */
  setInterval(() => {
    if (!port) return;
    if (node && node.isConnected) {
      // The overlay may be alive with only the subtitle log gone. That is when
      // YouTube swaps the whole right-hand column out.
      if (prefs.panel && !MimiPanel.mounted()) { MimiPanel.reset(); syncPanel(); }
      return;
    }
    if (mount()) startTick();
  }, 1500);

  window.addEventListener("resize", () => { if (ov) apply(); });
  document.addEventListener("fullscreenchange", () => { if (ov) apply(); });
})();
