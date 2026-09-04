/* The mimiwatch screen — player adapters: YouTube, Twitch and m3u8 in one
 * shape.
 *
 * This is web/app.js split up by concern. All of them are plain <script> tags,
 * read in the order index.html lists them, sharing one global scope -- module
 * syntax is avoided for the same reason as in overlay.js, which is shared with
 * the extension. Everything they call on each other is a function call at run
 * time, so the file order only has to keep main.js last.
 *
 * The screen asks the player four things and no more -- what second is it now
 * (getCurrentTime), go there (seekTo, playVideo), mute and unmute (setMuted),
 * clear yourself away (destroy). The method names are kept the same as the
 * YouTube IFrame API's so that the places calling those four (renderCue in
 * state.js, the line click in script-panel.js) do not have to change. In
 * multiview there is one adapter per tile and `state.player` is the focused
 * tile's.
 *
 *   { kind, ready, mount(host, src, {muted, onError}) -> Promise,
 *     load(src)?, getCurrentTime(), seekTo(t), playVideo(), setMuted(b), destroy() }
 *
 * `src` is the { site, video_id | channel | url } that srcOf() builds. */

/* Read an external script once only. YouTube's is read ahead of time by
 * index.html, but Twitch's and hls.js are read when the first tile needs them
 * -- there is no reason to make someone who never uses them download them.
 * Past 10 seconds it counts as a failure. */
const _scriptLoads = {};

function loadScriptOnce(url, isReady) {
  if (isReady && isReady()) return Promise.resolve();
  if (_scriptLoads[url]) return _scriptLoads[url];
  _scriptLoads[url] = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = url;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { delete _scriptLoads[url]; reject(new Error(t("adapter.error.scriptFailed", { url }))); };
    document.head.appendChild(s);
    setTimeout(() => {
      if (isReady && isReady()) resolve();
      else { delete _scriptLoads[url]; reject(new Error(t("adapter.error.scriptTimeout", { url }))); }
    }, 10000);
  });
  return _scriptLoads[url];
}

let _hostSeq = 0;

/* What to play, and from which site. Session status (/api/live/status), a
 * probe result, a multiview member -- put any of them in and the same answer
 * comes out. The server does tell us site, but on a new session it can be
 * empty because yt-dlp has not answered yet, so the URL is read as well. */
function srcOf(x) {
  if (!x) return { site: "none" };
  const url = x.url || "";
  const vid = x.video_id || (x.site === "youtube" ? x.id : "") || "";
  if (x.site === "youtube" || (!x.site && vid)) {
    return vid ? { site: "youtube", video_id: vid } : { site: "none" };
  }
  const tw = /twitch\.tv\/(?!videos\/)([A-Za-z0-9_]+)/i.exec(url);
  if (x.site === "twitch" || tw) {
    const channel = (x.channel || (tw ? tw[1] : "")).toLowerCase();
    return channel ? { site: "twitch", channel } : { site: "none" };
  }
  if (/\.m3u8(\?|$)/i.test(url)) return { site: "hls", url };
  return { site: "none" };
}

/* Clamp a seek in a live stream so it never lands past the live edge.
 *
 * Send a live embed past the edge and it sits down as "ended" -- YouTube stops
 * with nothing but a replay (↻) button left on screen, while the subtitles go
 * on updating (reported in 0.3.1). The only way back is to build the player
 * again (the state 0 watch in adapters), so it is better not to send it there
 * in the first place.
 *
 * The place that seeks is the subtitle line click (script-panel.js), but the
 * clamp lives in the adapter rather than there alone -- every click, including
 * the ones pressed while tiles are being moved or added, goes through this
 * door.
 *
 * Rewinding is left alone. What is blocked is **overshooting the edge**, and
 * only that. If the edge is unknown (before the metadata, getDuration() is 0)
 * there is no ground to clamp on, so the seek goes through as it is.
 */
const LIVE_EDGE_MARGIN_S = 5;

function liveSafeSeek(a, t, edgeOf) {
  const x = Number.isFinite(t) ? Math.max(0, t) : 0;
  if (!a.live) return x;
  let edge;
  try { edge = edgeOf(); } catch (_) { return x; }
  if (!Number.isFinite(edge) || edge <= 0) return x;
  return Math.min(x, Math.max(0, edge - LIVE_EDGE_MARGIN_S));
}

function adapterFor(src) {
  if (src.site === "youtube") return ytAdapter();
  if (src.site === "twitch" && typeof twitchAdapter === "function") return twitchAdapter();
  if (src.site === "hls" && typeof hlsAdapter === "function") return hlsAdapter();
  if (src.site === "media" && typeof mediaAdapter === "function") return mediaAdapter();
  return noneAdapter();
}

/* A tile with nothing to play -- a tab-audio session, a source that cannot be
 * embedded. `ready` stays false forever, so renderCue() draws no subtitles on
 * this tile (the same as the tab session of old). The notice is laid on by the
 * caller with playerError(). */
function noneAdapter() {
  return {
    kind: "none", ready: false, live: false,
    mount: async () => {},
    getCurrentTime: () => 0, seekTo: () => {}, playVideo: () => {},
    setMuted: () => {}, destroy: () => {},
  };
}

/* YouTube. The IFrame API **replaces** the element handed to it with an
 * iframe, so one empty element with a unique id is made per tile. A tile that
 * does not hold the focus autoplays muted -- a browser allows autoplay only
 * without sound. The focused tile is built with no such argument, as before,
 * and the user presses play. */
function ytAdapter() {
  const a = { kind: "youtube", ready: false, live: false, player: null };
  a.mount = async (host, src, opts = {}) => {
    a.live = !!opts.live;
    await whenApiReady();
    if (!(window.YT && window.YT.Player)) {
      throw new Error(t("adapter.youtube.apiFailed"));
    }
    const el = document.createElement("div");
    el.id = "yt-host-" + (++_hostSeq);
    host.appendChild(el);
    // fs:0 removes YouTube's fullscreen button. That button makes the
    // **iframe** the fullscreen element, and a browser draws only the subtree
    // of the fullscreen element, so the subtitle overlay that sits outside the
    // iframe disappears entirely. Inside the iframe is cross-origin and the
    // button cannot be intercepted, so it is removed and our own button put
    // there instead.
    const vars = { rel: 0, modestbranding: 1, playsinline: 1, fs: 0 };
    if (opts.muted) { vars.mute = 1; vars.autoplay = 1; }
    else if (opts.autoplay) vars.autoplay = 1;      // autoplay with sound -- only goes through right after a user gesture
    await new Promise((resolve) => {
      let done = false;
      const settle = () => { if (!done) { done = true; resolve(); } };
      a.player = new YT.Player(el.id, {
        videoId: src.video_id, playerVars: vars,
        events: {
          onReady: () => {
            a.ready = true;
            // fs:0 only removes the button. With allowfullscreen taken off,
            // the iframe cannot become the fullscreen element by any route at
            // all -- that way the state where the subtitles disappear is never
            // reached in the first place.
            const f = host.querySelector("iframe");
            if (f) f.removeAttribute("allowfullscreen");
            // A tile without the focus autoplays without sound. autoplay in
            // playerVars alone sometimes does not start it (a player built
            // late, with no user gesture), so it is asked once more here -- a
            // browser does not block muted playback.
            if (opts.muted) { a.player.mute(); a.player.playVideo(); }
            settle();
          },
          onError: (e) => {
            if (opts.onError) opts.onError(embedErrorText(e.data), src.video_id);
            settle();
          },
        },
      });
      // A video whose embedding is blocked may never send onReady. This
      // keeps whoever waits on the mount from standing there forever.
      setTimeout(settle, 15000);
    });
    // The buffering watch. After a tile was closed or the tiles were moved
    // around in multiview, a live player left behind could get caught
    // buffering and spin forever (reproduced by deleting one of two embeds of
    // the same stream; a reload cleared it). Buffering for more than 15
    // seconds re-attaches the stream inside the same iframe -- what a reload
    // did, done for that tile alone. Once a minute at most, and live only.
    if (opts.live) {
      let buffering = 0, healed = 0, stage = 0, ended = 0;
      const diag = () => {
        const p = a.player, f = host.querySelector("iframe");
        const g = (fn) => { try { return fn(); } catch (e) { return "err"; } };
        return {
          state: g(() => p.getPlayerState()), t: g(() => Math.round(p.getCurrentTime())),
          dur: g(() => Math.round(p.getDuration())), loaded: g(() => p.getVideoLoadedFraction()),
          muted: g(() => p.isMuted()), vol: g(() => p.getVolume()), q: g(() => p.getPlaybackQuality()),
          iframe: f ? { w: f.clientWidth, h: f.clientHeight, allow: f.getAttribute("allow"), connected: f.isConnected,
                        sameWin: g(() => p.getIframe() === f) } : null,
          page: { visible: document.visibilityState, focus: document.hasFocus(),
                  activation: navigator.userActivation ? [navigator.userActivation.hasBeenActive, navigator.userActivation.isActive] : null },
        };
      };
      a._watch = setInterval(() => {
        if (!a.player || !a.ready) return;
        let st;
        try { st = a.player.getPlayerState(); } catch (_) { return; }
        if (st === 1) { buffering = 0; stage = 0; ended = 0; return; }
        // State 0 is "ended" -- nothing but a replay (↻) button left on
        // screen, sitting still. A live stream has no reason to sit like that.
        // If the stream really has ended, the transcribing side ends with it,
        // but 0.3.1 reported the screen left in this state **while the
        // subtitles went on updating**. The buffering watch was looking at
        // state 3 only and missed this spot entirely -- it caught the stall
        // with a spinner turning, never the stall standing still.
        //
        // Three attempts at most (the 1st re-attaches, the 2nd and 3rd
        // rebuild). This is so a genuinely finished stream does not get a new
        // player seated every 9 seconds. Once it plays again (state 1) the
        // count goes back to 0 and the three refill for next time.
        if (st === 0) {
          buffering = 0;
          if (ended >= 3 || Date.now() - healed < 9000) return;
          healed = Date.now();
          ended++;
          console.warn(`[yt] ${src.video_id} is live but sat down saying "ended" (attempt ${ended})`,
                       JSON.stringify(diag()));
          if (ended === 1) {
            // On a live stream loadVideoById goes to the edge. It leaves the iframe alone, so it is the cheapest.
            console.warn(`[yt] ${src.video_id} reattaching with loadVideoById`);
            try { a.player.loadVideoById(src.video_id); return; } catch (_) { /* on to the next stage */ }
          }
          const back = !!(navigator.userActivation && navigator.userActivation.hasBeenActive);
          console.warn(`[yt] ${src.video_id} rebuilding the player (${back ? "autoplay with sound" : "press ▶"})`);
          if (a._remount) a._remount(false, back);
          return;
        }
        if (st !== 3) { buffering = 0; return; }
        buffering += 3;
        // A stall right after a tile was closed or the layout changed is
        // almost certainly caused by that relayout (reproduced: close another
        // tile and the unmuted player stalls even while holding the data), so
        // only 3 seconds are watched.
        const recent = Date.now() - (window.__tilesChangedAt || 0) < 20000;
        if (buffering < (recent ? 3 : 6) || Date.now() - healed < 9000) return;
        healed = Date.now();
        buffering = 0;
        stage++;
        const d = diag();
        console.warn(`[yt] ${src.video_id} buffering for over ${recent ? "3 s after a reorder" : "6 s"} (attempt ${stage})`, JSON.stringify(d));
        // A browser that has hit a stall right after a relayout once builds
        // the focused player anew, up front, on every later relayout
        // (applyLayout in tiles.js). An environment that never stalls carries
        // no such mark and so never flickers.
        if (recent && d.muted === false) {
          try { savePrefs({ ...loadPrefs(), ytRelayoutStall: true }); } catch (_) { /* recovery happens even if the save does not */ }
        }
        // What stalls was almost always the player **with the sound on**
        // (move the focus and the spinner follows it). The diagnostics say
        // media was arriving (loaded>0) and there had been a user gesture, so
        // it is not autoplay being blocked. It looks as though the YouTube
        // embed, realigning the stream across the "muted playback -> sound on"
        // switch, overshoots the live end (t > dur) and gets caught.
        // Re-attaching to the same player does not clear it, so the player is
        // built anew -- 1st: autoplay with the sound on (allowed thanks to the
        // gesture just before), 2nd: the play-button state (the user presses
        // inside the iframe = what a reload did). For a muted player's stall,
        // re-attaching to the same player was enough.
        if (d.muted === true) {
          console.warn(`[yt] ${src.video_id} reattaching with loadVideoById`);
          try { a.player.loadVideoById(src.video_id); } catch (_) { /* on to the next stage */ }
          return;
        }
        const autoplay = stage === 1 && !!(navigator.userActivation && navigator.userActivation.hasBeenActive);
        console.warn(`[yt] ${src.video_id} rebuilding the player (${autoplay ? "autoplay with sound" : "press ▶"})`);
        if (a._remount) a._remount(false, autoplay);
        if (!autoplay) stage = 0;
      }, 3000);
    }
  };
  /* Called by stage 2 of the watch: hung here so the caller (mountTile in tiles.js) can seat this tile again. */
  a._remount = null;
  a.load = (src) => { if (a.player && a.ready) a.player.loadVideoById(src.video_id); };
  a.getCurrentTime = () => (a.player && a.ready ? a.player.getCurrentTime() : 0);
  // A live stream's edge is getDuration() -- YouTube answers with the time elapsed since the stream began.
  a.seekTo = (t) => {
    if (!a.player || !a.ready) return;
    a.player.seekTo(liveSafeSeek(a, t, () => a.player.getDuration()), true);
  };
  a.playVideo = () => { if (a.player && a.ready) a.player.playVideo(); };
  a.setMuted = (m) => {
    if (!a.player || !a.ready) return;
    if (m) a.player.mute(); else a.player.unMute();
  };
  a.destroy = () => {
    if (a._watch) { clearInterval(a._watch); a._watch = null; }
    try { if (a.player) a.player.destroy(); } catch (_) { /* an iframe already gone */ }
    a.player = null;
    a.ready = false;
  };
  return a;
}

/* Raw m3u8. The server has been transcribing these all along, but the screen
 * was a black box -- there is no video id to put in the YouTube player. Now
 * hls.js is attached to a <video> and it plays. hls.js is bundled in
 * /static/vendor/ (Apache-2.0) and read when the first tile needs it. Safari
 * plays HLS by itself, and there it is simply handed the src.
 *
 * CORS is out of our hands. If the stream's server blocks a cross-origin fetch,
 * hls.js cannot open it -- even then the server transcribes with ffmpeg, so
 * the subtitle log on the right piles up as usual. That is what the notice
 * says. */
function hlsAdapter() {
  const a = { kind: "hls", ready: false, live: false, video: null, hls: null };
  a.mount = async (host, src, opts = {}) => {
    a.live = !!opts.live;
    const v = document.createElement("video");
    v.playsInline = true;
    v.controls = true;
    v.setAttribute("controlslist", "nofullscreen");   // fullscreen goes through our button -- the subtitles have to grow with it
    v.muted = !!opts.muted;
    v.autoplay = true;
    host.appendChild(v);
    a.video = v;
    const fail = (why) => {
      if (opts.onError) {
        opts.onError(why ? t("adapter.hls.openFailedReason", { reason: why })
                         : t("adapter.hls.openFailed"));
      }
    };
    if (v.canPlayType("application/vnd.apple.mpegurl")) {
      v.src = src.url;
      v.addEventListener("error", () => fail(t("adapter.hls.reason.native")), { once: true });
    } else {
      try {
        await loadScriptOnce("/static/vendor/hls.min.js", () => !!window.Hls);
      } catch (err) {
        fail(err.message);
        return;
      }
      if (!(window.Hls && Hls.isSupported())) { fail(t("adapter.hls.reason.noMse")); return; }
      a.hls = new Hls({ lowLatencyMode: true, enableWorker: true });
      a.hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data && data.fatal) fail(data.details || data.type);
      });
      a.hls.loadSource(src.url);
      a.hls.attachMedia(v);
    }
    a.ready = true;
    v.play().catch(() => { /* if autoplay is blocked the user presses play */ });
  };
  a.getCurrentTime = () => (a.video ? a.video.currentTime : 0);
  // A live <video> has duration Infinity. The edge is the end of the last seekable range.
  a.seekTo = (t) => {
    if (!a.video) return;
    a.video.currentTime = liveSafeSeek(a, t, () => {
      const r = a.video.seekable;
      return r && r.length ? r.end(r.length - 1) : a.video.duration;
    });
  };
  a.playVideo = () => { if (a.video) a.video.play().catch(() => {}); };
  a.setMuted = (m) => { if (a.video) a.video.muted = !!m; };
  a.destroy = () => {
    try { if (a.hls) a.hls.destroy(); } catch (_) { /* already closed */ }
    a.hls = null;
    if (a.video) { a.video.pause(); a.video.removeAttribute("src"); a.video.remove(); }
    a.video = null;
    a.ready = false;
  };
  return a;
}

/* Twitch. The official Embed JS (player.twitch.tv/js/embed/v1.js) is read when
 * the first tile needs it. Laying down a bare iframe is another road, but the
 * documented way to mute and unmute exists only on this one (setMuted).
 * `parent` is our page's host name -- the server opens on localhost and
 * 127.0.0.1 only, and Twitch allows both of those as a parent.
 *
 * Twitch's iframe comes out carrying allowfullscreen. It is taken off so that
 * the player's fullscreen button does not blow up the iframe alone -- that
 * makes the subtitles disappear. If it cannot be taken off, the guard in
 * onFullscreenChange puts things back. */
function twitchAdapter() {
  const a = { kind: "twitch", ready: false, live: false, player: null, obs: null };
  a.mount = async (host, src, opts = {}) => {
    a.live = !!opts.live;
    await loadScriptOnce("https://player.twitch.tv/js/embed/v1.js",
                         () => !!(window.Twitch && window.Twitch.Player));
    if (!(window.Twitch && window.Twitch.Player)) {
      throw new Error(t("adapter.twitch.loadFailed"));
    }
    const el = document.createElement("div");
    el.id = "tw-host-" + (++_hostSeq);
    host.appendChild(el);
    const strip = () => host.querySelectorAll("iframe").forEach(f => {
      f.removeAttribute("allowfullscreen");
      f.removeAttribute("allow");
    });
    a.obs = new MutationObserver(strip);
    a.obs.observe(el, { childList: true, subtree: true });
    await new Promise((resolve) => {
      let done = false;
      const settle = () => { if (!done) { done = true; resolve(); } };
      a.player = new Twitch.Player(el.id, {
        channel: src.channel, parent: [location.hostname],
        width: "100%", height: "100%", autoplay: true, muted: !!opts.muted,
      });
      a.player.addEventListener(Twitch.Player.READY, () => {
        a.ready = true;
        strip();
        a.player.setMuted(!!opts.muted);
        settle();
      });
      a.player.addEventListener(Twitch.Player.OFFLINE, () => {
        if (opts.onError) opts.onError(t("adapter.twitch.offline", { channel: src.channel }));
        settle();
      });
      setTimeout(settle, 15000);
    });
  };
  a.getCurrentTime = () => (a.player && a.ready ? (a.player.getCurrentTime() || 0) : 0);
  a.seekTo = (t) => {
    if (!a.player || !a.ready) return;
    a.player.seek(liveSafeSeek(a, t, () => a.player.getDuration()));
  };
  a.playVideo = () => { if (a.player && a.ready) a.player.play(); };
  a.setMuted = (m) => { if (a.player && a.ready) a.player.setMuted(!!m); };
  a.destroy = () => {
    if (a.obs) a.obs.disconnect();
    a.obs = null;
    try { if (a.player && a.player.destroy) a.player.destroy(); } catch (_) { /* already gone */ }
    a.player = null;
    a.ready = false;
  };
  return a;
}


/* A local file. The server's /api/media/<id> hands out the original over
 * Range, so it is bitten straight onto a <video> -- the hls adapter with hls.js
 * taken out. Audio files (mp3 and the like) play through <video> too: the
 * picture is black with only the controls showing, but the subtitle overlay
 * sits on top of it, so that black box is if anything exactly where the
 * subtitles belong. */
function mediaAdapter() {
  const a = { kind: "media", ready: false, live: false, video: null };
  a.mount = async (host, src, opts = {}) => {
    const v = document.createElement("video");
    v.playsInline = true;
    v.controls = true;
    v.setAttribute("controlslist", "nofullscreen");   // fullscreen goes through our button -- the subtitles have to grow with it
    v.muted = !!opts.muted;
    v.preload = "metadata";
    v.src = src.url;
    v.addEventListener("error", () => {
      if (opts.onError) {
        opts.onError(t("adapter.media.playbackFailed"));
      }
    });
    host.appendChild(v);
    a.video = v;
    a.ready = true;
  };
  a.load = (src) => {
    if (!a.video) return;
    a.video.src = src.url;
    a.video.load();
  };
  a.getCurrentTime = () => (a.video ? a.video.currentTime : 0);
  a.seekTo = (t) => { if (a.video) a.video.currentTime = Math.max(0, t || 0); };
  a.playVideo = () => { if (a.video) a.video.play().catch(() => {}); };
  a.setMuted = (m) => { if (a.video) a.video.muted = !!m; };
  a.destroy = () => {
    if (a.video) { a.video.pause(); a.video.removeAttribute("src"); a.video.remove(); }
    a.video = null;
    a.ready = false;
  };
  return a;
}
