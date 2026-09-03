/* Strings shared with the extension (overlay, popup, content, panel,
 * service worker). Byte-identical copy in ext/ -- see bench/ext_check.py. */
MW_I18N.add({
  /* content.js -- the bar drawn over the YouTube player. */
  "content.askKeep": {
    en: "Keep",
    ko: "계속",
  },
  "content.askMoved": {
    en: "Looks like you moved to a different video. Keep these subtitles on it?",
    ko: "다른 영상으로 옮긴 것 같습니다. 이 자막을 계속 얹을까요?",
  },
  "content.askTakeDown": {
    en: "Take down",
    ko: "내리기",
  },
  "content.noteTakenDown": {
    en: "A different video, so the subtitles came down. You can pick them again in the popup.",
    ko: "다른 영상이라 자막을 내렸습니다. 팝업에서 다시 고를 수 있습니다.",
  },

  /* panel.js -- the subtitle log in the chat column. Keyed `extpanel.` so it
   * cannot collide with the page's own script panel. */
  "extpanel.count": {
    en: "{n} lines",
    ko: "{n}줄",
  },
  "extpanel.follow": {
    en: "Follow",
    ko: "따라가기",
  },
  "extpanel.title": {
    en: "Subtitle log",
    ko: "자막 내역",
  },

  /* popup.js, and the service worker's answers -- those land on the popup's
   * error line, so they are the popup's strings too. */
  "popup.dim": {
    en: "Background",
    ko: "배경",
  },
  "popup.errAttach": {
    en: "Could not attach it",
    ko: "붙이지 못했습니다",
  },
  "popup.errCookies": {
    en: "Could not hand the cookies over",
    ko: "쿠키를 넘기지 못했습니다",
  },
  "popup.errNoAudio": {
    en: "Could not take the sound",
    ko: "소리를 잡지 못했습니다",
  },
  "popup.errNoAudioResume": {
    en: "Could not take the sound. Press “Stop transcribing” and then “Resume” again.",
    ko: "소리를 잡지 못했습니다. 「중단」한 뒤 다시 「이어받기」를 누르십시오.",
  },
  "popup.errNoLoginCookies": {
    en: "You do not seem to be signed in to YouTube (no login cookie).",
    ko: "유튜브에 로그인되어 있지 않은 것 같습니다 (로그인 쿠키가 없음).",
  },
  "popup.errNoResponse": {
    en: "no response",
    ko: "응답 없음",
  },
  "popup.errNoServer": {
    en: "Could not reach the server: {error} — check the address in the “Server” field below. Change it and it reconnects right away.",
    ko: "서버에 닿지 못했습니다: {error} — 아래 「서버」 칸의 주소를 확인하십시오. 바꾸면 곧바로 다시 붙습니다.",
  },
  "popup.errNotLive": {
    en: "Not a live stream. A VOD is added on the mimiwatch page.",
    ko: "라이브가 아닙니다. 녹화본은 mimiwatch 페이지에서 추가하십시오.",
  },
  "popup.errNotSession": {
    en: "Not a session being transcribed.",
    ko: "받아 적는 중인 세션이 아닙니다.",
  },
  "popup.errNotYouTube": {
    en: "Open this on a YouTube tab to lay subtitles on it.",
    ko: "유튜브 탭에서 열어야 자막을 얹을 수 있습니다.",
  },
  "popup.errResume": {
    en: "Could not resume it",
    ko: "이어받지 못했습니다",
  },
  "popup.errStart": {
    en: "Could not start it",
    ko: "시작하지 못했습니다",
  },
  "popup.errStop": {
    en: "Could not stop it",
    ko: "중단하지 못했습니다",
  },
  "popup.errUnknownRequest": {
    en: "Unknown request: {type}",
    ko: "모르는 요청: {type}",
  },
  "popup.fromTab": {
    en: "From this tab's sound",
    ko: "이 탭 소리로",
  },
  "popup.fromUrl": {
    en: "From this URL",
    ko: "주소로",
  },
  "popup.fromUrlWithCookies": {
    en: "🔑 Hand over login cookies, from this URL",
    ko: "🔑 로그인 쿠키 넘기고 주소로",
  },
  "popup.fromUrlWithCookiesTitle": {
    en: "Hands this browser's YouTube login cookies to the server and starts from this URL. For members-only streams. It amounts to running yt-dlp as your everyday account, so YouTube may put a bot check or a temporary block on it",
    ko: "이 브라우저의 유튜브 로그인 쿠키를 서버에 넘기고 주소로 시작합니다. 멤버십 전용 방송용. 평소 계정으로 yt-dlp 를 돌리는 셈이라 유튜브가 봇 확인이나 일시 제한을 걸 수 있습니다",
  },
  "popup.genre": {
    en: "Genre",
    ko: "장르",
  },
  "popup.genreGeneral": {
    en: "General",
    ko: "일반",
  },
  "popup.hide": {
    en: "Hide from page",
    ko: "화면에서 내리기",
  },
  "popup.hideTitle": {
    en: "Hides it from the page only; transcription keeps going",
    ko: "화면에서만 내립니다. 받아 적기는 계속됩니다",
  },
  "popup.hintEnded": {
    en: "The stream is over. Transcribing the whole video is done on the mimiwatch page.",
    ko: "끝난 방송입니다. 전체 영상 전사는 mimiwatch 페이지에서 합니다.",
  },
  "popup.hintProfile": {
    en: "Splits speech once it runs past {max} s. A pause counts at {min} s.",
    ko: "발화가 {max}초를 넘으면 끊습니다. 쉼은 {min}초.",
  },
  "popup.hintProfileNone": {
    en: "Sets how many seconds of speech to split at.",
    ko: "발화를 몇 초에 끊을지 정합니다.",
  },
  "popup.hintPushingCookies": {
    en: "Handing the login cookies over…",
    ko: "로그인 쿠키를 넘기는 중…",
  },
  "popup.hintResumedTab": {
    en: "Taking this tab's sound again. It appends after the subtitles already piled up.",
    ko: "이 탭의 소리를 다시 받습니다. 쌓인 자막 뒤에 이어 붙습니다.",
  },
  "popup.hintResumedUrl": {
    en: "Receiving again. If the gap falls inside the rewind window, it is filled in with nothing missing.",
    ko: "이어서 받는 중입니다. 멈춘 사이가 되감기 창 안이면 빠진 것 없이 메워집니다.",
  },
  "popup.hintResumePick": {
    en: "A stopped stream is selected. It appends to that same session, from whichever source you press.",
    ko: "멈춘 방송을 골랐습니다. 누르는 쪽의 소리 출처로 같은 세션에 이어 붙입니다.",
  },
  "popup.hintResumePickStream": {
    en: "A stopped stream is selected. It appends to that same session, from whichever source you press (it stopped because the stream cut out).",
    ko: "멈춘 방송을 골랐습니다. 누르는 쪽의 소리 출처로 같은 세션에 이어 붙입니다 (수신이 끊겨 멈춤).",
  },
  "popup.hintResuming": {
    en: "Resuming…",
    ko: "이어받는 중…",
  },
  "popup.hintStart": {
    en: "Received from the URL, the server keeps receiving even if you close the browser. Take this tab's sound for a stream the server cannot reach, members-only and the like.",
    ko: "주소로 받으면 브라우저를 닫아도 서버가 계속 받습니다. 멤버십 전용처럼 서버가 받지 못하는 방송은 탭 소리로 받으십시오.",
  },
  "popup.hintStarted": {
    en: "Receiving.",
    ko: "받는 중입니다.",
  },
  "popup.hintStartedNeedsReload": {
    en: "Receiving. Reload this tab to lay it on the page.",
    ko: "받는 중입니다. 화면에 얹으려면 이 탭을 새로고침하십시오.",
  },
  "popup.hintStartedReloaded": {
    en: "Receiving. Reloaded the page to lay it on.",
    ko: "받는 중입니다. 페이지를 새로고침해 얹었습니다.",
  },
  "popup.hintStarting": {
    en: "Starting…",
    ko: "시작하는 중…",
  },
  "popup.hintStopped": {
    en: "Stopped. The subtitles piled up so far stay where they are.",
    ko: "중단했습니다. 쌓인 자막은 그대로 남아 있습니다.",
  },
  /* Language names. The page has its own `lang.*` in
   * web/app/strings/core.js, which the popup does not load -- so the popup
   * carries its own copies. */
  "popup.lang.auto": {
    en: "Auto-detect",
    ko: "자동 판별",
  },
  "popup.lang.en": {
    en: "English",
    ko: "English",
  },
  "popup.lang.ja": {
    en: "Japanese",
    ko: "日本語",
  },
  "popup.lang.ko": {
    en: "Korean",
    ko: "한국어",
  },
  "popup.lang.zh": {
    en: "Chinese",
    ko: "中文",
  },
  "popup.mode.both": {
    en: "Both",
    ko: "둘 다",
  },
  "popup.mode.off": {
    en: "Off",
    ko: "끄기",
  },
  "popup.mode.source": {
    en: "Source only",
    ko: "원문만",
  },
  "popup.mode.translation": {
    en: "Translation only",
    ko: "번역만",
  },
  "popup.offset": {
    en: "Offset",
    ko: "오프셋",
  },
  "popup.panel": {
    en: "Subtitle log in the chat column",
    ko: "자막 내역을 채팅 자리에",
  },
  "popup.pick": {
    en: "What to overlay",
    ko: "무엇을 얹을까요",
  },
  "popup.pickLines": {
    en: "{title} · {n} lines",
    ko: "{title} · {n}줄",
  },
  "popup.pickLinesEnded": {
    en: "{title} · {n} lines · stream over",
    ko: "{title} · {n}줄 · 끝난 방송",
  },
  "popup.pickLinesStopped": {
    en: "{title} · {n} lines · stopped",
    ko: "{title} · {n}줄 · 멈춤",
  },
  "popup.pickNone": {
    en: "— Nothing chosen —",
    ko: "— 고르지 않음 —",
  },
  "popup.profile": {
    en: "Content type",
    ko: "콘텐츠 유형",
  },
  "popup.profileBroadcast": {
    en: "General stream",
    ko: "일반 방송",
  },
  "popup.refine": {
    en: "Polish with refined lines",
    ko: "정제본으로 다듬기",
  },
  /* The refinement hint is two sentences and the second is bold, so it is two
   * keys -- applyStatic writes text, and a key on the parent <p> would wipe
   * the <b> child. */
  "popup.refineHint": {
    en: "Switched on, it waits for one utterance group to finish, joins it and transcribes it again. The context is longer so it is more accurate, but the subtitle settles late and a line that is already up changes.",
    ko: "켜면 발화 한 무리가 끝나기를 기다렸다 합쳐서 다시 받아 적습니다. 문맥이 길어져 정확해지지만 자막이 늦게 자리를 잡고 이미 뜬 줄이 바뀝니다.",
  },
  "popup.refineHintLive": {
    en: "On live it is usually better left switched off.",
    ko: "라이브에서는 대개 꺼 두는 편이 낫습니다.",
  },
  "popup.resetPos": {
    en: "⤾ Reset position",
    ko: "⤾ 위치 되돌리기",
  },
  "popup.resumeFromTab": {
    en: "▶ Resume (from this tab's sound)",
    ko: "▶ 이어받기 (이 탭 소리로)",
  },
  "popup.resumeFromUrl": {
    en: "▶ Resume (from this URL)",
    ko: "▶ 이어받기 (주소로)",
  },
  "popup.resumeWithCookies": {
    en: "🔑 Hand over login cookies and resume (from this URL)",
    ko: "🔑 로그인 쿠키 넘기고 이어받기 (주소로)",
  },
  "popup.server": {
    en: "Server",
    ko: "서버",
  },
  "popup.serverTitle": {
    en: "The mimiwatch server's address. Change it here if you brought it up on a different port (e.g. http://localhost:8901). Change it and it reconnects right away",
    ko: "mimiwatch 서버 주소. 다른 포트에 띄웠으면 여기서 바꿉니다 (예: http://localhost:8901). 바꾸면 곧바로 다시 붙습니다",
  },
  "popup.show": {
    en: "Put back on page",
    ko: "화면에 다시 얹기",
  },
  "popup.showPrev": {
    en: "Keep the previous line",
    ko: "직전 문장 남기기",
  },
  "popup.showTitle": {
    en: "Lays what you picked back on this tab",
    ko: "고른 것을 이 탭에 다시 얹습니다",
  },
  "popup.size": {
    en: "Font size",
    ko: "글자 크기",
  },
  "popup.sourceLang": {
    en: "Language",
    ko: "언어",
  },
  "popup.startBox": {
    en: "Transcribe anew on this tab",
    ko: "이 탭에서 새로 받아 적기",
  },
  "popup.stateCues": {
    en: "{n} subtitle lines",
    ko: "자막 {n}줄",
  },
  "popup.stateNoClock": {
    en: "clock stopped",
    ko: "시계 멈춤",
  },
  "popup.stateNoCue": {
    en: "no subtitle in the current segment",
    ko: "지금 구간에 자막 없음",
  },
  "popup.stateNoPlayer": {
    en: "player not found",
    ko: "플레이어 못 찾음",
  },
  "popup.stateNoRoom": {
    en: "no room on screen",
    ko: "화면에 자리 없음",
  },
  "popup.stateNotAttached": {
    en: "Not attached to this tab yet.",
    ko: "이 탭에는 아직 붙지 않았습니다.",
  },
  "popup.stateOff": {
    en: "subtitles off",
    ko: "자막 끔",
  },
  "popup.statePanelNoRoom": {
    en: "no place for the subtitle log",
    ko: "자리 못 찾음",
  },
  "popup.statePanelUp": {
    en: "subtitle log up",
    ko: "자막 내역 세움",
  },
  "popup.statePickToOverlay": {
    en: "Pick one and it goes onto this tab.",
    ko: "고르면 이 탭에 얹습니다.",
  },
  "popup.stateReceiving": {
    en: "receiving",
    ko: "받는 중",
  },
  "popup.stateReloaded": {
    en: "Reloaded the page to lay it on.",
    ko: "페이지를 새로고침해 얹었습니다.",
  },
  "popup.stateStalled": {
    en: "server connection lost (reconnecting)",
    ko: "서버 연결 끊김 (다시 붙는 중)",
  },
  "popup.stateStreamEnded": {
    en: "stream ended",
    ko: "종료된 방송",
  },
  "popup.stateVod": {
    en: "VOD",
    ko: "녹화본",
  },
  "popup.stop": {
    en: "■ Stop transcribing",
    ko: "■ 받아 적기 중단",
  },
  "popup.stopTitle": {
    en: "Ends this tab's session on the server",
    ko: "이 탭의 세션을 서버에서 끝냅니다",
  },
  "popup.viewer": {
    en: "My language",
    ko: "내 언어",
  },
});
