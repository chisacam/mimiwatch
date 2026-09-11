/* Strings for the player area. Filled by the i18n pass. */
MW_I18N.add({
  // Live session states. The key suffix is the server's own state word
  // (state.js looks them up by it), so these six names are a closed set.
  "live.state.error": { en: "error", ko: "오류" },
  "live.state.interrupted": { en: "interrupted (server restarted)", ko: "중단됨 (서버 재시작)" },
  "live.state.loading": { en: "opening the model", ko: "모델 여는 중" },
  "live.state.running": { en: "receiving", ko: "수신 중" },
  "live.state.starting": { en: "starting", ko: "시작하는 중" },
  "live.state.stopped": { en: "ended", ko: "종료됨" },

  "adapter.error.scriptFailed": {
    en: "Could not load {url}",
    ko: "{url} 을 불러오지 못했습니다",
  },
  "adapter.error.scriptTimeout": {
    en: "{url} did not arrive within 10 seconds",
    ko: "{url} 이 10초 안에 오지 않았습니다",
  },
  "adapter.hls.openFailed": {
    en: "The browser could not open this stream directly — the server transcribes it anyway, so the subtitles keep piling up.",
    ko: "브라우저가 이 스트림을 직접 열지 못했습니다 — 자막은 서버가 받아 적으므로 계속 쌓입니다.",
  },
  "adapter.hls.openFailedReason": {
    en: "The browser could not open this stream directly ({reason}) — the server transcribes it anyway, so the subtitles keep piling up.",
    ko: "브라우저가 이 스트림을 직접 열지 못했습니다 ({reason}) — 자막은 서버가 받아 적으므로 계속 쌓입니다.",
  },
  "adapter.hls.reason.native": {
    en: "native HLS",
    ko: "네이티브 HLS",
  },
  "adapter.hls.reason.noMse": {
    en: "no MSE",
    ko: "MSE 없음",
  },
  "adapter.media.playbackFailed": {
    en: "The browser could not play this file (the format cannot be opened). The subtitle log on the right is still readable.",
    ko: "브라우저가 이 파일을 재생하지 못했습니다 (형식을 열 수 없음). 자막 내역은 오른쪽에서 그대로 읽을 수 있습니다.",
  },
  "adapter.twitch.loadFailed": {
    en: "Could not load the Twitch player. Please check the network.",
    ko: "Twitch 플레이어를 불러오지 못했습니다. 네트워크를 확인해 주세요.",
  },
  "adapter.twitch.offline": {
    en: "Twitch channel {channel} is not live.",
    ko: "트위치 채널 {channel} 이 방송 중이 아닙니다.",
  },
  "adapter.youtube.apiFailed": {
    en: "Could not load the YouTube IFrame API. Please check the network.",
    ko: "YouTube IFrame API를 불러오지 못했습니다. 네트워크를 확인해 주세요.",
  },
  "live.asr.switchFailed": {
    en: "Could not switch the transcription engine — {error}",
    ko: "전사 엔진을 바꾸지 못했습니다 — {error}",
  },
  "live.error.sessionNotFound": {
    en: "The session cannot be found",
    ko: "세션을 찾을 수 없습니다",
  },
  "live.picker.receiving": {
    en: "receiving",
    ko: "받는 중",
  },
  "live.resume.busy": {
    en: "Resuming…",
    ko: "이어받는 중…",
  },
  "live.resume.button": {
    en: "Resume",
    ko: "이어받기",
  },
  "live.resume.ended": {
    en: "This stream has ended. You can transcribe the whole recording it left — ",
    ko: "끝난 방송입니다. 남은 녹화본을 통째로 전사할 수 있습니다 — ",
  },
  "live.resume.error": {
    en: "It stopped with an error. If the cause is gone you can resume — ",
    ko: "오류로 멈췄습니다. 원인이 사라졌으면 이어서 받을 수 있습니다 — ",
  },
  "live.resume.failed": {
    en: "Could not resume — {error}",
    ko: "이어받지 못했습니다 — {error}",
  },
  "live.resume.interrupted": {
    en: "The server stopped, so reception was cut off — ",
    ko: "서버가 멈춰 수신이 끊겼습니다 — ",
  },
  "live.resume.mic": {
    en: "Subtitle reception has stopped. Allow the microphone again and they keep piling up — ",
    ko: "자막 수신이 멈춰 있습니다. 마이크를 다시 허용하면 이어서 쌓입니다 — ",
  },
  "live.resume.stopped": {
    en: "Reception was stopped for this stream. If it is still on you can resume — ",
    ko: "수신을 멈춘 방송입니다. 아직 진행 중이면 이어서 받을 수 있습니다 — ",
  },
  "live.resume.tab": {
    en: "Subtitle reception has stopped. Share the tab again and they keep piling up — ",
    ko: "자막 수신이 멈춰 있습니다. 탭을 다시 공유하면 이어서 쌓입니다 — ",
  },
  "live.resume.unknown": {
    en: "Reception has stopped — ",
    ko: "수신이 멈춰 있습니다 — ",
  },
  "live.retranscribe.button": {
    en: "⟳ Transcribe the whole video",
    ko: "⟳ 전체 영상 전사",
  },
  "live.retranscribe.hint": {
    en: "If the stream has ended and left a recording, transcribes that whole video again",
    ko: "방송이 끝나 녹화본으로 남았으면, 그 영상 전체를 다시 전사합니다",
  },
  "live.retranscribe.title": {
    en: "Re-transcribe · {title}",
    ko: "다시 전사 · {title}",
  },
  "live.retranscribe.titlePlain": {
    en: "Re-transcribe",
    ko: "다시 전사",
  },
  "live.row.stopped": {
    en: "subtitles stopped",
    ko: "자막 중단",
  },
  "live.status.disconnected": {
    en: "Live connection lost",
    ko: "라이브 연결 끊김",
  },
  "live.status.engine": {
    en: "transcription <b>{engine}</b>",
    ko: "전사 <b>{engine}</b>",
  },
  "live.status.error": {
    en: "Live error",
    ko: "라이브 오류",
  },
  "live.status.headline": {
    en: "{state} · source <b>{src}</b> → <b>{viewer}</b>",
    ko: "{state} · 원본 <b>{src}</b> → <b>{viewer}</b>",
  },
  "live.status.interrupted": {
    en: "{state} · {n} lines are kept",
    ko: "{state} · {n}줄까지 남아 있습니다",
  },
  "live.status.lines": {
    en: "{n} lines",
    ko: "{n}줄",
  },
  "live.status.standby": {
    en: "<b>Standby</b> (receiving audio only)",
    ko: "<b>대기</b>(소리만 받는 중)",
  },
  "live.status.stopped": {
    en: "Subtitles stopped · the stream keeps playing",
    ko: "자막 중단됨 · 방송은 계속 재생됩니다",
  },
  "live.mic.reshare": {
    en: "You have to allow the microphone again to continue.",
    ko: "마이크를 다시 허용해야 이어집니다.",
  },
  "live.mic.reshareLong": {
    en: "You have to allow the microphone again to continue. Press “Resume” in the list again.",
    ko: "마이크를 다시 허용해야 이어집니다. 목록에서 「이어받기」를 다시 누르십시오.",
  },
  "live.tab.logOnly": {
    en: "Reception has stopped; you are looking at the subtitle log that piled up.",
    ko: "수신은 멈춰 있고, 쌓인 자막 내역만 보고 있습니다.",
  },
  "live.tab.reshare": {
    en: "You have to share the tab again to continue.",
    ko: "탭을 다시 공유해야 이어집니다.",
  },
  "live.tab.reshareLong": {
    en: "You have to share the tab again to continue. Press “Resume” in the list again.",
    ko: "탭을 다시 공유해야 이어집니다. 목록에서 「이어받기」를 다시 누르십시오.",
  },
  "player.embed.badId": {
    en: "The video address is not valid.",
    ko: "영상 주소가 올바르지 않습니다.",
  },
  "player.embed.blocked": {
    en: "This video is set up so that it cannot be embedded in another site "
      + "(members-only streams usually are). Keep it open on YouTube and read "
      + "the subtitle log on the right — the subtitles keep piling up.",
    ko: "이 영상은 다른 사이트에 끼워 넣을 수 없게 되어 있습니다 "
      + "(멤버십 전용 방송이 대개 그렇습니다). 유튜브에서 열어 두고 "
      + "오른쪽 스크립트를 읽으십시오 — 자막은 계속 쌓입니다.",
  },
  "player.embed.notFound": {
    en: "The video cannot be found. It is private or has been deleted.",
    ko: "영상을 찾을 수 없습니다. 비공개이거나 지워졌습니다.",
  },
  "player.embed.playbackFailed": {
    en: "The browser's player could not open this video.",
    ko: "브라우저의 재생기가 이 영상을 열지 못했습니다.",
  },
  "player.embed.unknown": {
    en: "The video cannot be played (code {code}).",
    ko: "영상을 재생할 수 없습니다 (code {code}).",
  },
  "player.error.openOnYouTube": {
    en: "Open on YouTube",
    ko: "유튜브에서 열기",
  },
  "player.fullscreen.enter": {
    en: "⛶ Fullscreen",
    ko: "⛶ 전체화면",
  },
  "player.fullscreen.exit": {
    en: "⛶ Windowed",
    ko: "⛶ 창으로",
  },
  "player.fullscreen.playerTookOver": {
    en: "The YouTube player opened its own fullscreen. Use the “Fullscreen” button below.",
    ko: "유튜브 플레이어가 자체 전체화면을 열었습니다. 아래 「전체화면」 단추를 쓰십시오.",
  },
  "player.fullscreen.unavailable": {
    en: "⛶ No fullscreen",
    ko: "⛶ 전체화면 불가",
  },
  "player.fullscreen.unsupported": {
    en: "This browser does not support fullscreen.",
    ko: "이 브라우저는 전체화면을 지원하지 않습니다.",
  },
  "player.lang.missing": {
    en: "Source <b>{src}</b> · there is no <b>{viewer}</b> translation (needs re-transcribing)",
    ko: "원본 <b>{src}</b> · <b>{viewer}</b> 번역본이 없습니다 (재전사 필요)",
  },
  "player.lang.same": {
    en: "Source <b>{src}</b> · same as your language, so <b>no translation</b>",
    ko: "원본 <b>{src}</b> · 내 언어와 같아 <b>번역 없음</b>",
  },
  "player.lang.translated": {
    en: "Source <b>{src}</b> → translated to <b>{viewer}</b>",
    ko: "원본 <b>{src}</b> → <b>{viewer}</b> 번역됨",
  },
  "tile.error.needLive": {
    en: "Open a live stream first. A tile attaches next to it.",
    ko: "먼저 라이브 방송을 여십시오. 타일은 그 옆에 붙습니다.",
  },
  "tile.error.tooMany": {
    en: "Up to four tiles.",
    ko: "타일은 넷까지입니다.",
  },
  "tile.error.vodNotAllowed": {
    en: "A recording cannot be attached as a tile — multiview takes live streams only.",
    ko: "녹화본은 타일로 붙일 수 없습니다 — 멀티뷰에는 라이브만 들어갑니다.",
  },
  "tile.state.lines": {
    en: "{n} lines",
    ko: "{n}줄",
  },
});
