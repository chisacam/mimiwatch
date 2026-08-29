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
            if (opts.muted) a.player.mute();
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
  };
  a.load = (src) => { if (a.player && a.ready) a.player.loadVideoById(src.video_id); };
  a.getCurrentTime = () => (a.player && a.ready ? a.player.getCurrentTime() : 0);
  a.seekTo = (t) => { if (a.player && a.ready) a.player.seekTo(t, true); };
  a.playVideo = () => { if (a.player && a.ready) a.player.playVideo(); };
  a.setMuted = (m) => {
    if (!a.player || !a.ready) return;
    if (m) a.player.mute(); else a.player.unMute();
  };
  a.destroy = () => {
    try { if (a.player) a.player.destroy(); } catch (_) { /* 이미 사라진 iframe */ }
    a.player = null;
    a.ready = false;
  };
  return a;
}
