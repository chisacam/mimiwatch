/* mimiwatch 화면 — 플레이어 어댑터: 유튜브·트위치·m3u8 을 한 모양으로.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다.
 *
 * 화면이 플레이어에게 묻는 것은 넷뿐입니다 -- 지금 몇 초인가(getCurrentTime),
 * 어디로 가라(seekTo·playVideo), 소리를 끄고 켜라(setMuted), 치워라(destroy).
 * 메서드 이름을 유튜브 IFrame API 와 같게 둔 것은 그 넷을 부르는 자리
 * (state.js 의 renderCue, script-panel.js 의 줄 클릭)를 고치지 않으려는 것입니다.
 * 멀티뷰에서는 타일마다 어댑터가 하나씩이고 `state.player` 는 초점 타일의 것입니다.
 *
 *   { kind, ready, mount(host, src, {muted, onError}) -> Promise,
 *     load(src)?, getCurrentTime(), seekTo(t), playVideo(), setMuted(b), destroy() }
 *
 * `src` 는 srcOf() 가 만든 { site, video_id | channel | url } 입니다. */

/* 외부 스크립트를 한 번만 읽습니다. 유튜브는 index.html 이 미리 읽지만
 * 트위치·hls.js 는 첫 타일이 필요로 할 때 읽습니다 -- 안 쓰는 사람에게까지
 * 내려받게 할 이유가 없습니다. 10초가 지나면 실패로 칩니다. */
const _scriptLoads = {};

function loadScriptOnce(url, isReady) {
  if (isReady && isReady()) return Promise.resolve();
  if (_scriptLoads[url]) return _scriptLoads[url];
  _scriptLoads[url] = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = url;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { delete _scriptLoads[url]; reject(new Error(`${url} 을 불러오지 못했습니다`)); };
    document.head.appendChild(s);
    setTimeout(() => {
      if (isReady && isReady()) resolve();
      else { delete _scriptLoads[url]; reject(new Error(`${url} 이 10초 안에 오지 않았습니다`)); }
    }, 10000);
  });
  return _scriptLoads[url];
}

let _hostSeq = 0;

/* 어느 사이트의 무엇을 틀어야 하는가. 세션 상태(/api/live/status)나 probe 결과,
 * 멀티뷰 멤버 -- 어느 것을 넣어도 같은 답이 나옵니다. 서버가 site 를 알려 주지만
 * 새 세션은 yt-dlp 가 답하기 전이라 비어 있을 수 있어 주소로도 짚습니다. */
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

function adapterFor(src) {
  if (src.site === "youtube") return ytAdapter();
  if (src.site === "twitch" && typeof twitchAdapter === "function") return twitchAdapter();
  if (src.site === "hls" && typeof hlsAdapter === "function") return hlsAdapter();
  return noneAdapter();
}

/* 틀 것이 없는 타일 -- 탭 소리 세션, 임베드할 수 없는 소스. `ready` 가 영영
 * false 라 renderCue() 가 이 타일에는 자막을 그리지 않습니다(예전의 탭 세션과
 * 같습니다). 안내문은 부르는 쪽이 playerError() 로 얹습니다. */
function noneAdapter() {
  return {
    kind: "none", ready: false,
    mount: async () => {},
    getCurrentTime: () => 0, seekTo: () => {}, playVideo: () => {},
    setMuted: () => {}, destroy: () => {},
  };
}

/* 유튜브. IFrame API 는 넘겨준 요소를 iframe 으로 **바꿔 치우므로** 타일마다
 * 고유 id 의 빈 요소를 하나 만들어 줍니다. 초점이 아닌 타일은 소리를 끈 채
 * 자동 재생합니다 -- 브라우저는 소리 없는 자동 재생만 허용합니다. 초점 타일은
 * 예전과 같이 아무 인자 없이 만들어 사용자가 재생을 누릅니다. */
function ytAdapter() {
  const a = { kind: "youtube", ready: false, player: null };
  a.mount = async (host, src, opts = {}) => {
    await whenApiReady();
    if (!(window.YT && window.YT.Player)) {
      throw new Error("YouTube IFrame API를 불러오지 못했습니다. 네트워크를 확인해 주세요.");
    }
    const el = document.createElement("div");
    el.id = "yt-host-" + (++_hostSeq);
    host.appendChild(el);
    // fs:0 은 유튜브의 전체화면 단추를 지웁니다. 그 단추는 **iframe**을
    // 전체화면 요소로 만드는데, 브라우저는 전체화면 요소의 하위 트리만
    // 그리므로 iframe 밖에 있는 자막 오버레이가 통째로 사라집니다.
    // iframe 안은 교차 출처라 그 단추를 가로챌 수 없으니, 지우고 우리
    // 단추를 대신 둡니다.
    const vars = { rel: 0, modestbranding: 1, playsinline: 1, fs: 0 };
    if (opts.muted) { vars.mute = 1; vars.autoplay = 1; }
    else if (opts.autoplay) vars.autoplay = 1;      // 소리 켠 자동 재생 -- 사용자 조작 직후에만 통합니다
    await new Promise((resolve) => {
      let done = false;
      const settle = () => { if (!done) { done = true; resolve(); } };
      a.player = new YT.Player(el.id, {
        videoId: src.video_id, playerVars: vars,
        events: {
          onReady: () => {
            a.ready = true;
            // fs:0 은 단추를 지울 뿐입니다. allowfullscreen 을 떼면 iframe은
            // 어떤 경로로도 전체화면 요소가 될 수 없습니다 -- 그래야 자막이
            // 사라지는 상태 자체가 만들어지지 않습니다.
            const f = host.querySelector("iframe");
            if (f) f.removeAttribute("allowfullscreen");
            // 초점이 아닌 타일은 소리 없이 자동 재생합니다. playerVars 의 autoplay 만으로는
            // 시작하지 않는 경우가 있어(사용자 조작 없이 뒤늦게 만들어진 플레이어) 여기서
            // 한 번 더 시킵니다 -- 음소거 재생은 브라우저가 막지 않습니다.
            if (opts.muted) { a.player.mute(); a.player.playVideo(); }
            settle();
          },
          onError: (e) => {
            if (opts.onError) opts.onError(embedErrorText(e.data), src.video_id);
            settle();
          },
        },
      });
      // 임베드가 막힌 영상은 onReady 가 오지 않을 수 있습니다. 마운트를
      // 기다리는 쪽이 영영 서 있지 않게 합니다.
      setTimeout(settle, 15000);
    });
    // 버퍼링 감시. 멀티뷰에서 타일을 닫거나 자리를 바꾼 뒤 남은 라이브 플레이어가 버퍼링에
    // 갇혀 영영 도는 일이 있었습니다(같은 방송의 임베드 둘 중 하나를 지웠을 때 재현됨;
    // 새로고침하면 풀림). 15초 넘게 버퍼링이면 같은 iframe 안에서 방송을 다시 붙입니다 --
    // 새로고침이 하던 일을 그 타일만 합니다. 1분에 한 번만, 라이브에만.
    if (opts.live) {
      let buffering = 0, healed = 0, stage = 0;
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
        if (st !== 3) { buffering = 0; if (st === 1) stage = 0; return; }
        buffering += 3;
        // 타일을 닫거나 배치를 바꾼 직후의 정지는 거의 확실히 그 재배치가 부른 것이므로(다른
        // 타일을 닫았을 때 소리 켠 플레이어가 데이터를 들고도 멎는 것이 재현됨) 3초만 봅니다.
        const recent = Date.now() - (window.__tilesChangedAt || 0) < 20000;
        if (buffering < (recent ? 3 : 6) || Date.now() - healed < 9000) return;
        healed = Date.now();
        buffering = 0;
        stage++;
        const d = diag();
        console.warn(`[yt] ${src.video_id} ${recent ? "재배치 뒤 3" : "6"}초 넘게 버퍼링 (${stage}번째)`, JSON.stringify(d));
        // 멎는 것은 거의 언제나 **소리를 켠** 플레이어였습니다(초점을 옮기면 스피너도 따라감).
        // 진단값은 미디어를 받고 있었고(loaded>0) 사용자 조작도 있었다고 하므로 자동 재생 차단은
        // 아닙니다. 유튜브 임베드가 「음소거 재생 → 소리 켜기」 전환에서 스트림을 다시 맞추다
        // 라이브 끝을 앞질러(t > dur) 갇히는 모양입니다. 같은 플레이어에 다시 붙여도 안 풀리므로
        // 플레이어를 새로 만듭니다 -- 1차: 소리 켠 채 자동 재생(직전 조작 덕에 허용됨),
        // 2차: 재생 단추 상태(사용자가 iframe 안에서 누름 = 새로고침이 하던 일).
        // 음소거 플레이어의 정지는 같은 플레이어에 다시 붙이는 것으로 충분했습니다.
        if (d.muted === true) {
          console.warn(`[yt] ${src.video_id} loadVideoById 로 다시 붙습니다`);
          try { a.player.loadVideoById(src.video_id); } catch (_) { /* 다음 단계로 */ }
          return;
        }
        const autoplay = stage === 1 && !!(navigator.userActivation && navigator.userActivation.hasBeenActive);
        console.warn(`[yt] ${src.video_id} 플레이어를 다시 만듭니다 (${autoplay ? "소리 켠 자동 재생" : "▶ 를 눌러 주십시오"})`);
        if (a._remount) a._remount(false, autoplay);
        if (!autoplay) stage = 0;
      }, 3000);
    }
  };
  /* 감시 2단계가 부릅니다: 부르는 쪽(tiles.js 의 mountTile)이 이 타일을 다시 앉힐 수 있게 걸어 둡니다. */
  a._remount = null;
  a.load = (src) => { if (a.player && a.ready) a.player.loadVideoById(src.video_id); };
  a.getCurrentTime = () => (a.player && a.ready ? a.player.getCurrentTime() : 0);
  a.seekTo = (t) => { if (a.player && a.ready) a.player.seekTo(t, true); };
  a.playVideo = () => { if (a.player && a.ready) a.player.playVideo(); };
  a.setMuted = (m) => {
    if (!a.player || !a.ready) return;
    if (m) a.player.mute(); else a.player.unMute();
  };
  a.destroy = () => {
    if (a._watch) { clearInterval(a._watch); a._watch = null; }
    try { if (a.player) a.player.destroy(); } catch (_) { /* 이미 사라진 iframe */ }
    a.player = null;
    a.ready = false;
  };
  return a;
}

/* 생 m3u8. 서버는 예전부터 받아 적었지만 화면은 검은 상자였습니다 -- 유튜브
 * 플레이어에 넣을 영상 id 가 없으니까요. 이제 <video> 에 hls.js 를 붙여 틉니다.
 * hls.js 는 /static/vendor/ 에 묶여 있고(Apache-2.0) 첫 타일이 필요로 할 때 읽습니다.
 * 사파리는 HLS 를 스스로 틀므로 그때는 그냥 src 로 줍니다.
 *
 * CORS 는 우리가 어쩔 수 없습니다. 방송 서버가 다른 출처의 fetch 를 막으면
 * hls.js 는 열지 못합니다 -- 그때도 자막은 서버가 ffmpeg 으로 받아 적으므로
 * 오른쪽 자막 내역은 그대로 쌓입니다. 그렇게 안내합니다. */
function hlsAdapter() {
  const a = { kind: "hls", ready: false, video: null, hls: null };
  a.mount = async (host, src, opts = {}) => {
    const v = document.createElement("video");
    v.playsInline = true;
    v.controls = true;
    v.setAttribute("controlslist", "nofullscreen");   // 전체화면은 우리 단추로 -- 자막이 함께 커져야 합니다
    v.muted = !!opts.muted;
    v.autoplay = true;
    host.appendChild(v);
    a.video = v;
    const fail = (why) => {
      if (opts.onError) {
        opts.onError("브라우저가 이 스트림을 직접 열지 못했습니다"
                     + (why ? ` (${why})` : "") + " — 자막은 서버가 받아 적으므로 계속 쌓입니다.");
      }
    };
    if (v.canPlayType("application/vnd.apple.mpegurl")) {
      v.src = src.url;
      v.addEventListener("error", () => fail("네이티브 HLS"), { once: true });
    } else {
      try {
        await loadScriptOnce("/static/vendor/hls.min.js", () => !!window.Hls);
      } catch (err) {
        fail(err.message);
        return;
      }
      if (!(window.Hls && Hls.isSupported())) { fail("MSE 없음"); return; }
      a.hls = new Hls({ lowLatencyMode: true, enableWorker: true });
      a.hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data && data.fatal) fail(data.details || data.type);
      });
      a.hls.loadSource(src.url);
      a.hls.attachMedia(v);
    }
    a.ready = true;
    v.play().catch(() => { /* 자동 재생이 막혔으면 사용자가 누릅니다 */ });
  };
  a.getCurrentTime = () => (a.video ? a.video.currentTime : 0);
  a.seekTo = (t) => { if (a.video) a.video.currentTime = t; };
  a.playVideo = () => { if (a.video) a.video.play().catch(() => {}); };
  a.setMuted = (m) => { if (a.video) a.video.muted = !!m; };
  a.destroy = () => {
    try { if (a.hls) a.hls.destroy(); } catch (_) { /* 이미 닫힘 */ }
    a.hls = null;
    if (a.video) { a.video.pause(); a.video.removeAttribute("src"); a.video.remove(); }
    a.video = null;
    a.ready = false;
  };
  return a;
}

/* 트위치. 공식 Embed JS(player.twitch.tv/js/embed/v1.js)를 첫 타일이 필요로 할 때 읽습니다.
 * iframe 만 얹는 길도 있지만 소리를 끄고 켜는 문서화된 방법이 이쪽뿐입니다
 * (setMuted). `parent` 는 우리 페이지의 호스트 이름 -- 서버는 localhost 와
 * 127.0.0.1 로만 열리고 트위치는 그 둘을 부모로 허용합니다.
 *
 * 트위치의 iframe 은 allowfullscreen 을 달고 나옵니다. 그것을 떼어 두어 플레이어의
 * 전체화면 단추가 iframe 만 키우지 않게 합니다 -- 그러면 자막이 사라집니다. 못
 * 떼어도 onFullscreenChange 의 가드가 되돌립니다. */
function twitchAdapter() {
  const a = { kind: "twitch", ready: false, player: null, obs: null };
  a.mount = async (host, src, opts = {}) => {
    await loadScriptOnce("https://player.twitch.tv/js/embed/v1.js",
                         () => !!(window.Twitch && window.Twitch.Player));
    if (!(window.Twitch && window.Twitch.Player)) {
      throw new Error("Twitch 플레이어를 불러오지 못했습니다. 네트워크를 확인해 주세요.");
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
        if (opts.onError) opts.onError(`트위치 채널 ${src.channel} 이 방송 중이 아닙니다.`);
        settle();
      });
      setTimeout(settle, 15000);
    });
  };
  a.getCurrentTime = () => (a.player && a.ready ? (a.player.getCurrentTime() || 0) : 0);
  a.seekTo = (t) => { if (a.player && a.ready) a.player.seek(t); };
  a.playVideo = () => { if (a.player && a.ready) a.player.play(); };
  a.setMuted = (m) => { if (a.player && a.ready) a.player.setMuted(!!m); };
  a.destroy = () => {
    if (a.obs) a.obs.disconnect();
    a.obs = null;
    try { if (a.player && a.player.destroy) a.player.destroy(); } catch (_) { /* 이미 사라짐 */ }
    a.player = null;
    a.ready = false;
  };
  return a;
}
