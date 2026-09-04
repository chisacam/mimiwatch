/* Strings for the core area. Filled by the i18n pass. */
MW_I18N.add({
  // The pop-out window when the session it was opened for is no longer there.
  "script.gone": { en: "This subtitle log is gone. Open it again from the main window.",
                   ko: "이 자막 내역은 더 이상 없습니다. 본 창에서 다시 여십시오." },
  // Collapsed forms of the two toggles. main.js writes them on a state change;
  // the expanded forms are header.videos / header.panel.
  "header.panel.collapsed": { en: "Subtitle log \u25c2", ko: "자막 내역 \u25c2" },
  "header.videos.collapsed": { en: "\u25b8 Videos", ko: "\u25b8 영상" },
  // The add dialog asks for a tile when it is opened from multiview.
  "add.title.tile": { en: "Add tile \u00b7 multiview", ko: "타일 추가 \u00b7 멀티뷰" },

  // The two "which language" questions are different axes: manage.myLanguage is
  // what subtitles get translated into, this one is what the app itself speaks.
  "manage.uiLanguage": { en: "App language", ko: "앱 언어" },
  "setup.uiLanguage": { en: "App language", ko: "앱 언어" },

  "add.asr": {
    en: "Transcription engine",
    ko: "전사 엔진"
  },
  "add.asr.hint": {
    en: "It starts with this engine the moment you add it. On a low-spec machine, pick the light side.",
    ko: "넣는 순간 이 엔진으로 시작합니다. 낮은 사양이라면 가벼운 쪽을 고르십시오."
  },
  "add.backend": {
    en: "Translation engine",
    ko: "번역 엔진"
  },
  "add.file": {
    en: "File",
    ko: "파일"
  },
  "add.file.hint": {
    en: "Anything ffmpeg opens works — mp3, wav, mp4, mkv and so on. When transcription finishes it plays on this screen with the subtitles.",
    ko: "mp3·wav·mp4·mkv 등 ffmpeg가 여는 것이면 됩니다. 전사가 끝나면 이 화면에서 자막과 함께 재생됩니다."
  },
  "add.genre": {
    en: "Genre",
    ko: "장르"
  },
  "add.genre.hint": {
    en: "Fits the translation prompt to the character of the speech. A tech talk and a game stream use different words.",
    ko: "번역 프롬프트를 발화의 성격에 맞춥니다. 기술 발표와 게임 방송은 쓰는 말이 다릅니다."
  },
  "add.hint": {
    en: "Put in a YouTube URL or an m3u8 URL and it fetches the audio, transcribes it, and translates it too if the source language differs from my language. Video and audio files on this machine work as well — pick them under \"Audio source\" below.",
    ko: "YouTube 주소나 m3u8 주소를 넣으면 오디오를 받아 전사하고, 원본 언어가 내 언어와 다르면 번역까지 진행합니다. 이 기계의 영상·음성 파일도 됩니다 — 아래 「소리 출처」에서 고르십시오."
  },
  "add.lang": {
    en: "Source language",
    ko: "원본 언어"
  },
  "add.lang.hint": {
    en: "For a video in a single language, specifying it is safer. Auto-detection can lock on to a different language over a stretch of noise.",
    ko: "단일 언어 영상이라면 지정하는 편이 안전합니다. 자동 판별은 잡음 구간에서 다른 언어로 고착될 수 있습니다."
  },
  "add.name": {
    en: "Name",
    ko: "이름"
  },
  "add.name.hint": {
    en: "Chrome does not tell us the title of the shared tab — write it yourself. Left empty it becomes \"Tab audio\", and you can fix it later with \"✎ Name\" at the top.",
    ko: "크롬은 공유한 탭의 제목을 알려 주지 않습니다 — 직접 적으십시오. 비우면 「탭 오디오」가 되고, 나중에 위쪽 「✎ 이름」으로 고칠 수 있습니다."
  },
  "add.name.placeholder": {
    en: "What you are listening to (the name it is left under in the list)",
    ko: "무엇을 듣는지 (목록에 이 이름으로 남습니다)"
  },
  "add.profile": {
    en: "Content type",
    ko: "콘텐츠 유형"
  },
  "add.profile.hint": {
    en: "Decides how often speech is cut. When several people talk over each other, cutting short reduces what is dropped.",
    ko: "발화를 얼마나 자주 끊을지 정합니다. 여러 사람이 겹쳐 말하면 짧게 끊어야 누락이 줄어듭니다."
  },
  "add.refine": {
    en: "Polish with refined lines",
    ko: "정제본으로 다듬기"
  },
  "add.refine.hint": {
    en: "Switched on, it waits for one utterance group to finish, joins it and transcribes it again. The context is longer so it is more accurate, but the subtitle settles late and a line that is already up changes. Switched off, it cuts short and sends each out immediately.",
    ko: "켜면 발화 한 무리가 끝나기를 기다렸다 합쳐서 다시 받아 적습니다. 문맥이 길어져 정확해지지만 자막이 늦게 자리를 잡고 이미 뜬 줄이 바뀝니다. 끄면 짧게 끊어 바로 내보냅니다."
  },
  "add.refine.tag": {
    en: "transcription is 30–50% longer",
    ko: "전사가 30~50% 깁니다"
  },
  "add.source": {
    en: "Audio source",
    ko: "소리 출처"
  },
  "add.source.file": {
    en: "A video or audio file on this machine",
    ko: "이 기계의 영상·음성 파일"
  },
  "add.source.hint.url": {
    en: "The server fetches the audio directly with yt-dlp.",
    ko: "서버가 yt-dlp로 오디오를 직접 받습니다."
  },
  "add.source.tab": {
    en: "Sound from another tab in this browser",
    ko: "이 브라우저의 다른 탭 소리"
  },
  "add.source.url": {
    en: "Fetch from a URL (default)",
    ko: "주소에서 받기 (기본)"
  },
  "add.speakers": {
    en: "Attach speaker tags",
    ko: "화자 태그 붙이기"
  },
  "add.start": {
    en: "Start",
    ko: "시작"
  },
  "add.tag.liveOnly": {
    en: "live only",
    ko: "라이브 전용"
  },
  "add.tag.vodOnly": {
    en: "VOD only",
    ko: "녹화본 전용"
  },
  "add.tileHint": {
    en: "Attaches next to the stream you are watching (YouTube, Twitch, m3u8 live). Sound and subtitles come from the focused tile only — press a tile, or use the 1~4 keys, to move it. A stream that is not focused only takes the sound in, and once the focus arrives it transcribes from a few seconds back.",
    ko: "지금 보는 방송 옆에 붙습니다(YouTube·Twitch·m3u8 라이브). 소리와 자막은 초점 타일만 -- 타일을 누르거나 1~4 키로 옮깁니다. 초점이 아닌 방송은 소리만 받아 두다가 초점이 오면 직전 몇 초부터 받아 적습니다."
  },
  "add.title": {
    en: "Add video",
    ko: "영상 추가"
  },
  "add.url": {
    en: "URL",
    ko: "주소"
  },
  "add.url.placeholder": {
    en: "https://youtu.be/... or https://.../index.m3u8",
    ko: "https://youtu.be/... 또는 https://.../index.m3u8"
  },
  "controls.addTile": {
    en: "⊞ Add tile",
    ko: "⊞ 타일 추가"
  },
  "controls.addTile.title": {
    en: "Attaches another live stream next to the one you are watching (up to 4). Sound and subtitles come from the focused tile only",
    ko: "지금 보는 방송 옆에 다른 라이브를 붙입니다 (최대 4). 소리와 자막은 초점 타일만"
  },
  "controls.background": {
    en: "Background",
    ko: "배경"
  },
  "controls.cueReset": {
    en: "⤾ Reset position",
    ko: "⤾ 위치 되돌리기"
  },
  "controls.cueReset.title": {
    en: "A subtitle can be dragged around over the video. This button puts it back at bottom centre",
    ko: "자막은 영상 위에서 끌어 옮길 수 있습니다. 이 단추는 아래 가운데로 되돌립니다"
  },
  "controls.fontSize": {
    en: "Font size",
    ko: "글자 크기"
  },
  "controls.fullscreen": {
    en: "⛶ Fullscreen",
    ko: "⛶ 전체화면"
  },
  "controls.fullscreen.title": {
    en: "Fullscreen (F). Enlarges the player and the subtitles together",
    ko: "전체화면 (F). 플레이어와 자막을 함께 키웁니다"
  },
  "controls.layout": {
    en: "Layout",
    ko: "배치"
  },
  "controls.layout.horizontal": {
    en: "Horizontal",
    ko: "좌우"
  },
  "controls.layout.vertical": {
    en: "Vertical",
    ko: "상하"
  },
  "controls.mode.both": {
    en: "Both",
    ko: "둘 다"
  },
  "controls.mode.off": {
    en: "Off",
    ko: "끄기"
  },
  "controls.mode.source": {
    en: "Source only",
    ko: "원문만"
  },
  "controls.mode.translation": {
    en: "Translation only",
    ko: "번역만"
  },
  "controls.offset": {
    en: "Offset",
    ko: "오프셋"
  },
  "controls.showPrev": {
    en: "Keep the previous line",
    ko: "직전 문장 남기기"
  },
  "controls.subtitle": {
    en: "Subtitles",
    ko: "자막"
  },
  "dialog.cancel": {
    en: "Cancel",
    ko: "취소"
  },
  "engine.apiKey": {
    en: "API key",
    ko: "API 키"
  },
  "engine.apiKey.placeholder": {
    en: "(optional)",
    ko: "(선택)"
  },
  "engine.back": {
    en: "Back",
    ko: "뒤로"
  },
  "engine.baseUrl": {
    en: "URL",
    ko: "주소"
  },
  "engine.id": {
    en: "Engine ID",
    ko: "엔진 ID"
  },
  "engine.label": {
    en: "Display name",
    ko: "표시 이름"
  },
  "engine.minChars": {
    en: "Skip short utterances",
    ko: "짧은 발화 건너뛰기"
  },
  "engine.minChars.hint": {
    en: "0 translates everything. Raise it only for a paid API that charges per call.",
    ko: "0이면 모두 번역합니다. 호출당 요금이 붙는 유료 API에서만 올리십시오."
  },
  "engine.model": {
    en: "Model",
    ko: "모델"
  },
  "engine.submit": {
    en: "Save",
    ko: "저장"
  },
  "engine.title": {
    en: "Add engine",
    ko: "엔진 추가"
  },
  "engine.window": {
    en: "Window length (s)",
    ko: "창 길이(초)"
  },
  "engine.window.hint": {
    en: "The length of audio sent at once. Match it to the server's request size limit.",
    ko: "한 번에 보낼 오디오 길이입니다. 서버의 요청 크기 제한에 맞추십시오."
  },
  "export.download": {
    en: "Download",
    ko: "내려받기"
  },
  "export.fmt": {
    en: "Format",
    ko: "형식"
  },
  "export.fmt.json": {
    en: "JSON — re-importing · re-translating",
    ko: "JSON — 다시 불러오기·재번역"
  },
  "export.fmt.srt": {
    en: "SRT — video editors · players",
    ko: "SRT — 영상 편집기·플레이어"
  },
  "export.fmt.txt": {
    en: "Text — reading with timestamps attached",
    ko: "텍스트 — 타임스탬프를 붙여 읽기"
  },
  "export.fmt.vtt": {
    en: "WebVTT — web players",
    ko: "WebVTT — 웹 플레이어"
  },
  "export.hint": {
    en: "The end time runs until the next subtitle, 6 seconds at most.",
    ko: "끝 시각은 다음 자막까지, 최대 6초입니다."
  },
  "export.title": {
    en: "Export subtitles",
    ko: "자막 내보내기"
  },
  "export.view": {
    en: "What to include",
    ko: "담을 것"
  },
  "export.view.both": {
    en: "Both (translation on the line after the source)",
    ko: "둘 다 (원문 다음 줄에 번역)"
  },
  "fs.cueReset.title": {
    en: "Puts the subtitle back at bottom centre. The position itself is moved by dragging the subtitle",
    ko: "자막을 아래 가운데로 되돌립니다. 자리는 자막을 끌어서 옮깁니다"
  },
  "fs.exit": {
    en: "⛶ To window",
    ko: "⛶ 창으로"
  },
  "header.live.stop": {
    en: "Stop",
    ko: "중단"
  },
  "header.manage": {
    en: "Manage ▾",
    ko: "관리 ▾"
  },
  "header.now.empty": {
    en: "Pick a video, or add one",
    ko: "영상을 고르거나 추가하십시오"
  },
  "header.panel": {
    en: "Subtitle log ▸",
    ko: "자막 내역 ▸"
  },
  "header.panel.title": {
    en: "Collapse/expand the subtitle log (S)",
    ko: "자막 내역 접기·펼치기 (S)"
  },
  "header.quit": {
    en: "⏻ Shut down",
    ko: "⏻ 종료"
  },
  "header.quit.title": {
    en: "Closes the streams being received, stops the server, then closes this window",
    ko: "받는 중인 방송을 닫고 서버를 멈춘 뒤 이 창을 닫습니다"
  },
  "header.rename": {
    en: "✎ Name",
    ko: "✎ 이름"
  },
  "header.rename.title": {
    en: "Renames this session",
    ko: "이 세션의 이름을 바꿉니다"
  },
  "header.videos": {
    en: "◧ Videos",
    ko: "◧ 영상"
  },
  "header.videos.title": {
    en: "Collapse/expand the video list (V)",
    ko: "영상 목록 접기·펼치기 (V)"
  },
  "job.cancel": {
    en: "Stop",
    ko: "중단"
  },
  "job.retranslating": {
    en: "Re-translating…",
    ko: "재번역 중…"
  },
  "lang.auto": {
    en: "Auto-detect",
    ko: "자동 판별"
  },
  "lang.en": {
    en: "English",
    ko: "English"
  },
  "lang.ja": {
    en: "Japanese",
    ko: "日本語"
  },
  "lang.ko": {
    en: "Korean",
    ko: "한국어"
  },
  "lang.zh": {
    en: "Chinese",
    ko: "中文"
  },
  "library.add": {
    en: "＋ Add",
    ko: "＋ 추가"
  },
  "library.add.title": {
    en: "Add a new video by YouTube URL",
    ko: "YouTube 주소로 새 영상 추가"
  },
  "library.title": {
    en: "Videos",
    ko: "영상"
  },
  "manage.asr": {
    en: "Transcription",
    ko: "전사"
  },
  "manage.engines": {
    en: "⚙ Engines",
    ko: "⚙ 엔진 관리"
  },
  "manage.myLanguage": {
    en: "My language",
    ko: "내 언어"
  },
  "manage.translation": {
    en: "Translation",
    ko: "번역"
  },
  "model.back": {
    en: "Back",
    ko: "뒤로"
  },
  "model.device": {
    en: "Device",
    ko: "장치"
  },
  "model.device.auto": {
    en: "Auto (GPU if there is a GPU)",
    ko: "자동 (GPU가 있으면 GPU)"
  },
  "model.file": {
    en: "File",
    ko: "파일"
  },
  "model.file.hint": {
    en: "The name of the .gguf file as it appears in the repository's \"Files\" tab.",
    ko: "저장소의 「Files」 탭에 보이는 .gguf 파일 이름입니다."
  },
  "model.file.placeholder": {
    en: "e.g. whisper-large-v3-turbo-Q8_0.gguf",
    ko: "예: whisper-large-v3-turbo-Q8_0.gguf"
  },
  "model.id": {
    en: "Engine ID",
    ko: "엔진 ID"
  },
  "model.id.placeholder": {
    en: "(made from the file name if left empty)",
    ko: "(비우면 파일 이름에서 만듭니다)"
  },
  "model.kind": {
    en: "Kind",
    ko: "종류"
  },
  "model.kind.asr": {
    en: "Transcription (transcribe.cpp GGUF — whisper · sensevoice · moonshine family)",
    ko: "전사 (transcribe.cpp GGUF — whisper·sensevoice·moonshine 계열)"
  },
  "model.kind.tr": {
    en: "Translation (llama.cpp chat GGUF — gemma and the like)",
    ko: "번역 (llama.cpp 채팅 GGUF — gemma 등)"
  },
  "model.label": {
    en: "Display name",
    ko: "표시 이름"
  },
  "model.label.placeholder": {
    en: "(the file name if left empty)",
    ko: "(비우면 파일 이름)"
  },
  "model.repo": {
    en: "Repository",
    ko: "저장소"
  },
  "model.repo.placeholder": {
    en: "owner/name  e.g. handy-computer/whisper-large-v3-turbo-gguf",
    ko: "소유자/이름  예: handy-computer/whisper-large-v3-turbo-gguf"
  },
  "model.submit": {
    en: "Start downloading",
    ko: "받기 시작"
  },
  "model.title": {
    en: "Add a model from Hugging Face",
    ko: "허깅페이스에서 모델 추가"
  },
  "model.token": {
    en: "Hugging Face token",
    ko: "허깅페이스 토큰"
  },
  /* Split around the <code>HF_TOKEN</code> in the markup: applyStatic writes
   * textContent, so a nested element cannot sit inside one key's text. */
  "model.token.hint.1": {
    en: "It is not stored; it is used for this download only. The environment variable",
    ko: "저장하지 않습니다. 이번 내려받기에만 씁니다. 환경변수"
  },
  "model.token.hint.2": {
    en: ", if set, is what is used.",
    ko: "이 있으면 그것을 씁니다."
  },
  "model.token.placeholder": {
    en: "(optional) only for repositories that need a licence agreement",
    ko: "(선택) 라이선스 동의가 필요한 저장소만"
  },
  "panel.edit.add": {
    en: "＋ Write a line",
    ko: "＋ 줄 쓰기"
  },
  "panel.edit.add.title": {
    en: "Writes a new subtitle line at the current playback time",
    ko: "지금 재생 위치의 시각으로 새 자막 줄을 씁니다"
  },
  "panel.edit.hint": {
    en: "Press a line to fix it; write a new one where it is missing",
    ko: "줄을 눌러 고치고, 빈 곳은 새로 씁니다"
  },
  "panel.export": {
    en: "⤓ Export",
    ko: "⤓ 내보내기"
  },
  "panel.export.title": {
    en: "Exports the accumulated subtitles as a file",
    ko: "쌓인 자막을 파일로 내보냅니다"
  },
  "panel.follow": {
    en: "Follow",
    ko: "따라가기"
  },
  "panel.mode.edit": {
    en: "✎ Edit",
    ko: "✎ 편집"
  },
  "panel.mode.edit.title": {
    en: "Pressing a line edits the source, the translation and the time",
    ko: "줄을 누르면 원문·번역·시각을 고칩니다"
  },
  "panel.mode.read": {
    en: "Read",
    ko: "읽기"
  },
  "panel.mode.read.title": {
    en: "Pressing a line moves the video to that point",
    ko: "줄을 누르면 그 지점으로 이동합니다"
  },
  "panel.mode.tr": {
    en: "⟳ Translate",
    ko: "⟳ 번역"
  },
  "panel.mode.tr.title": {
    en: "Selects lines to translate again. Shift takes a range",
    ko: "줄을 골라 다시 번역합니다. shift로 구간을 잡습니다"
  },
  "panel.modes.aria": {
    en: "Subtitle log mode",
    ko: "자막 내역 모드"
  },
  "panel.popout": {
    en: "⧉ Pop out",
    ko: "⧉ 따로 띄우기"
  },
  "panel.popout.title": {
    en: "Opens the subtitle log on its own. For reading it while watching a stream whose embedding is blocked on YouTube",
    ko: "자막 내역만 따로 띄웁니다. 임베드가 막힌 방송을 유튜브에서 보며 읽을 때 씁니다"
  },
  "panel.title": {
    en: "Subtitle log",
    ko: "자막 내역"
  },
  "panel.tr.all": {
    en: "All",
    ko: "전체"
  },
  /* The count is rewritten as lines are selected; this is only what the strip
   * says before anything is. */
  "panel.tr.count.zero": {
    en: "0 lines selected",
    ko: "0줄 선택"
  },
  "panel.tr.go": {
    en: "⟳ Re-translate",
    ko: "⟳ 재번역"
  },
  "panel.tr.none": {
    en: "None",
    ko: "해제"
  },
  "settings.add": {
    en: "＋ Add",
    ko: "＋ 추가"
  },
  /* Split around the <code>/v1/audio/transcriptions</code> in the markup. */
  "settings.asr.hint.1": {
    en: "Turns speech into text. The default is local Whisper (transcribe.cpp). An external engine speaks the OpenAI-compatible",
    ko: "음성을 글자로 옮깁니다. 기본은 로컬 Whisper(transcribe.cpp)입니다. 외부 엔진은 OpenAI 호환"
  },
  "settings.asr.hint.2": {
    en: "protocol and works for VODs and live alike — in live a request goes out for every chunk of speech (2–12 s), so the subtitle is late by the round trip. It can be swapped in mid-stream.",
    ko: "규약을 쓰며 녹화본과 라이브 모두에 쓸 수 있습니다 — 라이브에서는 발화 한 조각(2~12초)마다 요청이 나가므로 왕복 시간만큼 자막이 늦습니다. 라이브 도중에도 갈아 끼울 수 있습니다."
  },
  "settings.asr.title": {
    en: "Transcription engines",
    ko: "전사 엔진"
  },
  "settings.close": {
    en: "Close",
    ko: "닫기"
  },
  "settings.cookies.delete": {
    en: "Delete",
    ko: "지우기"
  },
  "settings.cookies.hint": {
    en: "None. To receive a members-only stream by URL, press \"🔑 Hand over login cookies and use the URL\" in the extension popup — only then do this browser's cookies come to the server.",
    ko: "없음. 멤버십 전용 방송을 주소로 받으려면 확장 팝업의 「🔑 로그인 쿠키 넘기고 주소로」를 누르십시오 — 그때만 이 브라우저의 쿠키가 서버로 옵니다."
  },
  "settings.cookies.title": {
    en: "YouTube login cookies",
    ko: "유튜브 로그인 쿠키"
  },
  "settings.models.add": {
    en: "＋ Add from Hugging Face",
    ko: "＋ 허깅페이스에서 추가"
  },
  /* Split around the <code id="model-dir"> and the <b>ffmpeg</b> in the markup.
   * Part 3 opens with a colon so that it reads correctly right after "ffmpeg"
   * with no space in between -- the Korean particle there takes no space. */
  "settings.models.hint.1": {
    en: "Models live outside the program, in",
    ko: "모델은 프로그램 밖"
  },
  "settings.models.hint.2": {
    en: "— deleting the repository or swapping in a new version does not download them again. A download cut off partway resumes when you download it again.",
    ko: "에 둡니다. 저장소를 지우거나 새 판으로 바꿔도 다시 받지 않습니다. 받다 끊긴 것은 다시 받으면 이어 받습니다."
  },
  "settings.models.hint.3": {
    en: ": no download needed if the system already has it.",
    ko: "는 시스템에 있으면 받을 필요가 없습니다."
  },
  "settings.models.title": {
    en: "Models & Tools",
    ko: "모델 · 도구"
  },
  /* Split around the <b> and the <code>./run.sh</code> in the markup. */
  "settings.server.hint.1": {
    en: "It closes the streams being received",
    ko: "받는 중인 방송을"
  },
  "settings.server.hint.2": {
    en: "and then stops the server. Closing the window or killing the process leaves those sessions as \"interrupted\". To start it again, in a terminal:",
    ko: "서버를 멈춥니다. 창을 그냥 닫거나 프로세스를 죽이면 그 세션은 「중단됨」으로 남습니다. 다시 켜려면 터미널에서"
  },
  "settings.server.hint.strong": {
    en: "properly first",
    ko: "먼저 제대로 닫고"
  },
  "settings.server.title": {
    en: "Server",
    ko: "서버"
  },
  "settings.setup.hint": {
    en: "Picks the default transcription and translation engines again. It starts downloading the models the chosen combination needs.",
    ko: "기본 전사·번역 엔진을 다시 고릅니다. 고른 조합에 필요한 모델을 받기 시작합니다."
  },
  "settings.setupAgain": {
    en: "Run first-time setup again",
    ko: "초기 설정 다시"
  },
  "settings.shutdown": {
    en: "Shut down",
    ko: "종료"
  },
  "settings.title": {
    en: "Engines",
    ko: "엔진 관리"
  },
  /* Split around the <code>/v1/chat/completions</code> in the markup. */
  "settings.tr.hint.1": {
    en: "Turns the recognised sentences into the viewer's language. It speaks the OpenAI-compatible",
    ko: "인식한 문장을 시청자 언어로 옮깁니다. OpenAI 호환"
  },
  "settings.tr.hint.2": {
    en: "protocol.",
    ko: "규약을 씁니다."
  },
  "settings.tr.title": {
    en: "Translation backends",
    ko: "번역 백엔드"
  },
  "settings.update.check": {
    en: "Check for updates",
    ko: "새 판 확인"
  },
  "settings.update.title": {
    en: "Update",
    ko: "판올림"
  },
  "setup.go": {
    en: "Start with this",
    ko: "이대로 시작"
  },
  "setup.group.asr": {
    en: "Transcription (speech → text)",
    ko: "전사 (음성 → 글자)"
  },
  "setup.group.tr": {
    en: "Translation (source → my language)",
    ko: "번역 (원문 → 내 언어)"
  },
  /* Split around the <b> in the markup: the bold clause is the middle of one
   * sentence, so the third part opens where the bold leaves off. */
  "setup.hint.default": {
    en: "The default is the light CPU engines",
    ko: "기본은 가벼운 CPU 엔진"
  },
  "setup.hint.intro": {
    en: "Pick the engines to run on this machine.",
    ko: "이 기계에서 돌릴 엔진을 고릅니다."
  },
  "setup.hint.rest": {
    en: ", so it runs anywhere, but quality is better on the heavy side — if you have a GPU (Metal or Vulkan) and memory to spare, raise it. Only the models the chosen combination needs are downloaded. You can change it at any time later under \"Manage\".",
    ko: "이라 어디서든 돌지만 품질은 무거운 쪽이 낫습니다 — GPU(Metal·Vulkan)가 있고 메모리가 넉넉하면 올리십시오. 고른 조합에 필요한 모델만 받습니다. 나중에 「관리」에서 언제든 바꿀 수 있습니다."
  },
  "setup.later": {
    en: "Later",
    ko: "나중에"
  },
  "setup.notice.download": {
    en: "Download for the current settings",
    ko: "지금 설정대로 받기"
  },
  "setup.notice.models": {
    en: "Models & Tools",
    ko: "모델·도구"
  },
  "setup.notice.noModels": {
    en: "Nothing needed for transcription is here yet.",
    ko: "전사에 필요한 모델이 아직 없습니다."
  },
  "setup.notice.start": {
    en: "First-time setup",
    ko: "초기 설정"
  },
  "setup.title": {
    en: "First-time setup",
    ko: "초기 설정"
  },
  "tile.close.title": {
    en: "Closes this tile (and stops receiving its subtitles)",
    ko: "이 타일을 닫습니다 (자막 수신도 멈춥니다)"
  },
  "tile.cover.title": {
    en: "Press to move the focus to this stream. Drag to swap places with another tile",
    ko: "누르면 이 방송으로 초점을 옮깁니다. 끌어서 다른 타일과 자리를 바꿉니다"
  },
  "tile.sound.title": {
    en: "Sound and subtitles follow this tile",
    ko: "소리와 자막이 이 타일을 따릅니다"
  },
  "update.apply": {
    en: "Restart and apply",
    ko: "다시 시작하며 적용"
  },
  "update.download": {
    en: "Download",
    ko: "받기"
  },
  "update.later": {
    en: "Later",
    ko: "나중에"
  }
});
