/* Strings for the engines area. Filled by the i18n pass. */
MW_I18N.add({
  /* engines.js -- channel glossaries (docs/GLOSSARY.md) */
  "settings.glossary.title": {
    en: "Glossary",
    ko: "채널 시어집",
  },
  "settings.glossary.add": {
    en: "Add channel",
    ko: "채널 추가",
  },
  "settings.glossary.hint": {
    en: "Words kept per channel, written into the translation prompt — streamer names, brands, recurring catchphrases. It applies from the next line onward, to this channel's videos and live streams, on engines that use a prompt. The default engine (M2M-100) uses no prompt, so the glossary goes without it.",
    ko: "채널마다 유지하는 단어를 번역 프롬프트에 씁니다 — 스트리머 이름, 브랜드, 반복되는 밈. 프롬프트를 쓰는 엔진에서 이 채널의 영상과 라이브에, 다음 줄부터 적용됩니다. 기본 엔진(M2M-100)은 프롬프트를 쓰지 않아 시어집을 타지 못합니다.",
  },
  "settings.glossary.empty": {
    en: "No glossaries yet. Add a channel to keep its names as-is.",
    ko: "아직 시어집이 없습니다. 채널을 추가해 이름을 그대로 유지해 보십시오.",
  },
  "settings.glossary.row.terms": {
    en: "{n} terms",
    ko: "시어 {n}개",
  },
  "settings.glossary.form.add": {
    en: "Channel glossary",
    ko: "채널 시어집",
  },
  "settings.glossary.form.edit": {
    en: "Channel glossary — {name}",
    ko: "채널 시어집 — {name}",
  },
  "settings.glossary.form.back": {
    en: "Back",
    ko: "뒤로",
  },
  "settings.glossary.form.name": {
    en: "Channel name",
    ko: "채널 이름",
  },
  "settings.glossary.form.namePlaceholder": {
    en: "The name the list shows",
    ko: "목록에 보이는 이름",
  },
  "settings.glossary.form.key": {
    en: "Channel key",
    ko: "채널 키",
  },
  "settings.glossary.form.keyPlaceholder": {
    en: "e.g. youtube:UC…, twitch:… — blank keys by name",
    ko: "예: youtube:UC…, twitch:… — 비워두면 이름으로",
  },
  "settings.glossary.form.keyHint": {
    en: "Videos and streams of this channel match the key. A name a person typed keys as manual:name.",
    ko: "이 채널의 영상과 방송이 키로 맞습니다. 사람이 입력한 이름은 manual:이름 으로 키가 됩니다.",
  },
  "settings.glossary.form.terms": {
    en: "Terms — one per line",
    ko: "시어 — 한 줄에 하나씩",
  },
  "settings.glossary.form.termsPlaceholder": {
    en: "source → target\none term per line",
    ko: "원어 → 번역\n한 줄에 하나씩",
  },
  "settings.glossary.form.termsHint": {
    en: "Each line is source → target, written into the prompt as-is. The list keeps everything; the prompt carries up to 100 terms.",
    ko: "한 줄이 원어 → 번역이며 프롬프트에 그대로 씁니다. 목록은 전부 보관하고, 프롬프트에는 100개까지 씁니다.",
  },
  "settings.glossary.form.warn": {
    en: "The glossary reaches the translator by prompt. An engine that uses no prompt (the default M2M-100) ignores it, and a term is a hint — the model may still miss a line.",
    ko: "시어집은 프롬프트로 전달됩니다. 프롬프트를 쓰지 않는 엔진(기본 M2M-100)은 무시하고, 시어는 힌트라 모델이 일부 줄을 놓칠 수 있습니다.",
  },
  "settings.glossary.form.save": {
    en: "Save",
    ko: "저장",
  },
  "settings.glossary.form.delete": {
    en: "Delete",
    ko: "삭제",
  },
  "add.channel": {
    en: "Channel name",
    ko: "채널 이름",
  },
  "add.channel.placeholder": {
    en: "Only when we could not get it from the address",
    ko: "주소에서 알 수 없을 때만",
  },
  "add.channel.hint": {
    en: "It chooses the channel glossary. The address carries the channel on YouTube and Twitch; elsewhere, type it.",
    ko: "채널 시어집을 가립니다. 유튜브·트위치 주소는 채널을 담고, 그 밖에는 직접 입력합니다.",
  },
  "jobs.glossary": {
    en: "glossary {name} ({n} terms)",
    ko: "시어집 {name} (시어 {n}개)",
  },
  /* capture.js -- tab audio */
  "capture.error.getAudio": {
    en: "Could not take the tab's sound: {error}",
    ko: "탭 소리를 받지 못했습니다: {error}",
  },
  "capture.error.noAudio": {
    en: "No sound came with it. Pick the tab in the sharing window and switch on “Also share tab audio”.",
    ko: "소리가 함께 오지 않았습니다. 공유 창에서 탭을 고르고 「탭 오디오도 공유」를 켜 주십시오.",
  },
  "capture.notice.dropped": {
    en: "Transcription could not keep up with realtime, so {n}s were thrown away. Try a lighter transcription engine.",
    ko: "전사가 실시간을 따라가지 못해 {n}초를 버렸습니다. 가벼운 전사 엔진으로 바꿔 보십시오.",
  },
  "capture.notice.popupBlocked": {
    en: "The popup was blocked, so the subtitle log window did not open — open it with “⧉ Pop out” on the right.",
    ko: "팝업이 막혀 자막 내역 창을 띄우지 못했습니다 — 오른쪽 「⧉ 따로 띄우기」로 여십시오.",
  },
  "capture.notice.sessionEnded": {
    en: "The subtitle session ended: {error}",
    ko: "자막 세션이 끝났습니다: {error}",
  },
  "capture.notice.sharingEnded": {
    en: "Tab sharing ended. Subtitles are no longer being received.",
    ko: "탭 공유가 끝났습니다. 자막 수신을 멈췄습니다.",
  },
  "capture.notice.silent": {
    en: "No sound is coming from the tab. Check that it is playing, and that “Also share tab audio” was switched on when you shared it.",
    ko: "탭에서 소리가 오지 않습니다. 그 탭이 재생 중인지, 공유할 때 「탭 오디오도 공유」를 켰는지 확인하십시오.",
  },
  "capture.rename.placeholder": {
    en: "What you are listening to",
    ko: "무엇을 듣고 있는지",
  },
  "capture.stage.notice": {
    en: "This stream is not played here. Only its sound is being transcribed from another tab — watch it in the YouTube tab, and read the subtitle log in the side window or in the script panel on the right.",
    ko: "이 방송은 여기서 재생하지 않습니다. 소리만 다른 탭에서 받아 적고 있습니다 — 유튜브 탭에서 보시고, 자막 내역은 옆 창이나 오른쪽 스크립트에서 읽으십시오.",
  },
  "capture.stage.windowShare": {
    en: "You are sharing a window or the whole screen, not a tab — if other sound gets mixed in, pick the tab again.",
    ko: "지금은 탭이 아니라 창·화면을 공유하고 있습니다 — 다른 소리가 섞이면 탭으로 다시 고르십시오.",
  },
  "capture.tabAudio": {
    en: "Tab audio",
    ko: "탭 오디오",
  },

  /* engines.js -- engines, models & tools, first-time setup, cookies, shutdown */
  "engines.cookies.delete.confirm": {
    en: "Delete the YouTube login cookies held on the server? A stream being received goes on without them from its next reconnect.",
    ko: "서버에 있는 유튜브 로그인 쿠키를 지울까요? 받는 중인 방송은 다음 재접속부터 쿠키 없이 갑니다.",
  },
  "engines.cookies.none": {
    en: "None. To receive a members-only stream by URL, press “🔑 Hand over login cookies and use the URL” in the extension popup — only then do this browser's cookies come to the server.",
    ko: "없음. 멤버십 전용 방송을 주소로 받으려면 확장 팝업의 「🔑 로그인 쿠키 넘기고 주소로」를 누르십시오 — 그때만 이 브라우저의 쿠키가 서버로 옵니다.",
  },
  "engines.cookies.none.env": {
    en: "Using the file from the MIMIWATCH_YTDLP_COOKIES environment variable. To receive a members-only stream by URL, press “🔑 Hand over login cookies and use the URL” in the extension popup — only then do this browser's cookies come to the server.",
    ko: "환경변수 MIMIWATCH_YTDLP_COOKIES 의 파일을 씁니다. 멤버십 전용 방송을 주소로 받으려면 확장 팝업의 「🔑 로그인 쿠키 넘기고 주소로」를 누르십시오 — 그때만 이 브라우저의 쿠키가 서버로 옵니다.",
  },
  "engines.cookies.present": {
    en: "{n} cookies are on the server (taken {when}). yt-dlp uses them for a stream received by URL. Delete them once you are done — they are the keys to your account.",
    ko: "쿠키 {n}개가 서버에 있습니다 ({when}에 받음). 주소로 받는 방송에 yt-dlp 가 이 쿠키를 씁니다. 쓸 일이 끝났으면 지우십시오 — 계정의 열쇠입니다.",
  },
  "engines.form.add.asr": {
    en: "Add an engine · transcription",
    ko: "엔진 추가 · 전사",
  },
  "engines.form.add.tr": {
    en: "Add an engine · translation",
    ko: "엔진 추가 · 번역",
  },
  "engines.form.edit.asr": {
    en: "Edit an engine · transcription",
    ko: "엔진 수정 · 전사",
  },
  "engines.form.edit.tr": {
    en: "Edit an engine · translation",
    ko: "엔진 수정 · 번역",
  },
  "engines.model.cancel": {
    en: "Stop",
    ko: "중단",
  },
  "engines.model.delete.confirm.model": {
    en: "Delete the model “{label}”? ({size})\nIt has to be downloaded again to be used.",
    ko: "'{label}' 모델을(를) 지울까요? ({size})\n다시 쓰려면 다시 받아야 합니다.",
  },
  "engines.model.delete.confirm.tool": {
    en: "Delete the tool “{label}”? ({size})\nIt has to be downloaded again to be used.",
    ko: "'{label}' 도구을(를) 지울까요? ({size})\n다시 쓰려면 다시 받아야 합니다.",
  },
  "engines.model.download": {
    en: "Download",
    ko: "받기",
  },
  "engines.model.kind.asr": {
    en: "Transcription",
    ko: "전사",
  },
  "engines.model.kind.aux": {
    en: "Support",
    ko: "보조",
  },
  "engines.model.kind.other": {
    en: "Files not in the catalog",
    ko: "목록에 없는 파일",
  },
  "engines.model.kind.tool": {
    en: "Tools",
    ko: "도구",
  },
  "engines.model.kind.tr": {
    en: "Translation",
    ko: "번역",
  },
  "engines.model.required": {
    en: "{label} ·required",
    ko: "{label} ·필수",
  },
  "engines.model.resume": {
    en: "Resume",
    ko: "이어 받기",
  },
  "engines.model.retry": {
    en: "Again",
    ko: "다시",
  },
  "engines.model.state.downloaded": {
    en: "{size} taken",
    ko: "{size} 받음",
  },
  "engines.model.state.error": {
    en: "Failed",
    ko: "실패",
  },
  "engines.model.state.missing": {
    en: "Not here",
    ko: "없음",
  },
  "engines.model.state.partial": {
    en: "Partly taken, {size}",
    ko: "받다 만 것 {size}",
  },
  "engines.model.state.queued": {
    en: "Waiting",
    ko: "기다리는 중",
  },
  "engines.model.state.ready": {
    en: "Here",
    ko: "있음",
  },
  "engines.model.state.system": {
    en: "Using the system copy",
    ko: "시스템 것 사용",
  },
  "engines.picker.untranslated": {
    en: "{label} (not translated)",
    ko: "{label} (미번역)",
  },
  "engines.profile.option": {
    en: "{label} · split every {n}s",
    ko: "{label} · {n}초마다 끊기",
  },
  "engines.quit.button": {
    en: "⏻ Shut down",
    ko: "⏻ 종료",
  },
  "engines.quit.manual": {
    en: "The browser does not let a script close this tab. Please close it yourself.",
    ko: "이 탭은 브라우저가 스크립트로 닫게 두지 않습니다. 직접 닫으십시오.",
  },
  "engines.remove.confirm": {
    en: "Delete “{name}”?",
    ko: "'{name}' 을(를) 삭제할까요?",
  },
  "engines.row.delete": {
    en: "Delete",
    ko: "삭제",
  },
  "engines.row.edit": {
    en: "Edit",
    ko: "수정",
  },
  "engines.row.local": {
    en: "Runs locally · nothing sent out",
    ko: "로컬 실행 · 외부 전송 없음",
  },
  "engines.row.lockedTip": {
    en: "The default local engine cannot be deleted",
    ko: "기본 로컬 엔진은 삭제할 수 없습니다",
  },
  "engines.setup.downloading": {
    en: "Taking the models — {label} {pct}",
    ko: "모델을 받는 중입니다 — {label} {pct}",
  },
  "engines.setup.downloading.more": {
    en: "Taking the models — {label} {pct} ({n} to go)",
    ko: "모델을 받는 중입니다 — {label} {pct} (남은 것 {n}개)",
  },
  "engines.setup.fallbackName": {
    en: "{label} (translation fallback)",
    ko: "{label} (번역 대체용)",
  },
  "engines.setup.first": {
    en: "First run. Pick the engines that suit this machine. Not here: {list}",
    ko: "처음이시군요. 이 기계에 맞는 엔진을 고르십시오. 없는 것: {list}",
  },
  "engines.setup.gpuIfAvailable": {
    en: "GPU where there is one",
    ko: "GPU가 있으면 GPU",
  },
  "engines.setup.missing": {
    en: "Something needed is still not here: {list}",
    ko: "필요한 것이 아직 없습니다: {list}",
  },
  "engines.setup.queued": {
    en: "{n} models are waiting to be taken.",
    ko: "모델 {n}개가 내려받기를 기다리고 있습니다.",
  },
  "engines.setup.remote": {
    en: "Remote server · nothing to download · that server has to be up",
    ko: "원격 서버 · 받을 것 없음 · 그 서버가 떠 있어야 합니다",
  },
  "engines.setup.total": {
    en: "To take: {list} — about {size}",
    ko: "받을 것: {list} — 약 {size}",
  },
  "engines.setup.total.none": {
    en: "Every model needed is here. You can start right away.",
    ko: "필요한 모델이 다 있습니다. 바로 쓸 수 있습니다.",
  },
  "engines.setup.willDownload": {
    en: "{size} to take",
    ko: "{size} 받음",
  },
  "engines.shutdown.busy": {
    en: "Shutting down…",
    ko: "종료하는 중…",
  },
  "engines.shutdown.button": {
    en: "Shut down",
    ko: "종료",
  },
  "engines.shutdown.confirm": {
    en: "Shut the server down. A stream being received is closed with it.\n\nGo ahead?",
    ko: "서버를 종료합니다. 받는 중인 방송이 있으면 함께 닫습니다.\n\n계속할까요?",
  },
  "engines.shutdown.confirm.live": {
    en: "Close the stream being received and shut the server down. The subtitles transcribed so far are kept.\n\nGo ahead?",
    ko: "받는 중인 방송을 닫고 서버를 종료합니다. 여기까지 받아 적은 자막은 남습니다.\n\n계속할까요?",
  },
  "engines.shutdown.done": {
    en: "The server is shut down.",
    ko: "서버를 종료했습니다.",
  },
  "engines.shutdown.done.n": {
    en: "Closed {n} streams and shut the server down.",
    ko: "방송 {n}건을 닫고 서버를 종료했습니다.",
  },
  "engines.shutdown.doneLabel": {
    en: "Shut down",
    ko: "종료됨",
  },
  "engines.shutdown.failed": {
    en: "The server could not be stopped ({error}). It may be an older version that does not know this route. Shut it down with Ctrl-C in the terminal.",
    ko: "서버를 멈추지 못했습니다 ({error}). 서버가 이 기능을 모르는 예전 판일 수 있습니다. 터미널에서 Ctrl-C 로 끄십시오.",
  },
  "engines.shutdown.restart.bundle": {
    en: "To start it again, run mimiwatch again.",
    ko: "다시 켜려면 mimiwatch 를 다시 실행하십시오.",
  },
  "engines.shutdown.restart.repo": {
    en: "To start it again, run ./run.sh in the terminal.",
    ko: "다시 켜려면 터미널에서 ./run.sh 를 실행하십시오.",
  },
  "engines.shutdown.stale": {
    en: "This tab no longer updates.",
    ko: "이 탭은 더 이상 갱신되지 않습니다.",
  },

  /* jobs.js -- background transcription and translation */
  "jobs.cancelled": {
    en: "Stopped",
    ko: "중단됨",
  },
  "jobs.cancelled.saved": {
    en: "Stopped · saved up to {done}/{total}",
    ko: "중단됨 · {done}/{total}까지 저장",
  },
  "jobs.cancelling": {
    en: "Stopping…",
    ko: "중단하는 중…",
  },
  "jobs.count.seconds": {
    en: "{done}/{total}s",
    ko: "{done}/{total}초",
  },
  "jobs.count.skipped": {
    en: "{done}/{total}  ({skipped} skipped)",
    ko: "{done}/{total}  (건너뜀 {skipped})",
  },
  "jobs.done.count": {
    en: "Done, {n} lines · {secs}s",
    ko: "완료 {n}건 · {secs}초",
  },
  "jobs.done.count.degraded": {
    en: "Done, {n} lines · {secs}s  · {failures} remote failures, fell back to local",
    ko: "완료 {n}건 · {secs}초  · 원격 실패 {failures}회, 로컬로 대체됨",
  },
  "jobs.done.elapsed": {
    en: "Done · {secs}s",
    ko: "완료 · {secs}초",
  },
  "jobs.error.noFile": {
    en: "Pick a file.",
    ko: "파일을 골라 주십시오.",
  },
  "jobs.error.noUrl": {
    en: "Put a URL in.",
    ko: "주소를 넣어 주십시오.",
  },
  "jobs.error.tileLiveOnly": {
    en: "Only a live stream can be added as a tile.",
    ko: "라이브 방송만 타일로 붙일 수 있습니다.",
  },
  "jobs.error.upload": {
    en: "The upload failed",
    ko: "업로드가 실패했습니다",
  },
  "jobs.error.uploadReply": {
    en: "The upload's answer could not be read",
    ko: "업로드 응답을 읽지 못했습니다",
  },
  "jobs.failed": {
    en: "Failed",
    ko: "실패",
  },
  "jobs.otherWindow": {
    en: "{label} (another window)",
    ko: "{label} (다른 창)",
  },
  "jobs.phase.convert": {
    en: "Converting the audio",
    ko: "오디오 변환 중",
  },
  "jobs.phase.done": {
    en: "Done",
    ko: "완료",
  },
  "jobs.phase.download": {
    en: "Taking the audio",
    ko: "오디오 내려받는 중",
  },
  "jobs.phase.probe": {
    en: "Checking the video",
    ko: "영상 정보 확인",
  },
  "jobs.phase.transcribe": {
    en: "Transcribing",
    ko: "전사 중",
  },
  "jobs.phase.translate": {
    en: "Translating",
    ko: "번역 중",
  },
  "jobs.retranslating": {
    en: "Re-translating…",
    ko: "재번역 중…",
  },
  "jobs.source.asrFallback": {
    en: "The external transcription engine failed · fell back to the local engine",
    ko: "외부 전사 엔진 실패 · 로컬 엔진으로 대체",
  },
  "jobs.source.degraded": {
    en: "No answer from the remote engine ({failures} failures) · {n} lines fell back to local",
    ko: "원격 응답 없음 (실패 {failures}회) · 로컬 대체 {n}건",
  },
  "jobs.source.degraded.short": {
    en: "No answer from the remote engine · {n} lines fell back to local",
    ko: "원격 응답 없음 · 로컬 대체 {n}건",
  },
  "jobs.source.hint.file": {
    en: "Transcribes a video or audio file on this machine. A copy of the file you pick is left in the server's storage (data/uploads).",
    ko: "이 기계의 영상·음성 파일을 받아 적습니다. 고른 파일은 서버 저장소(data/uploads)에 사본이 남습니다.",
  },
  "jobs.source.hint.tab": {
    en: "Chromium-based browsers only. Pick the tab in the sharing window and switch on “Also share tab audio”. It is the route for what the server cannot take, such as a members-only stream.",
    ko: "크롬 계열 전용입니다. 공유 창에서 탭을 고르고 「탭 오디오도 공유」를 켜십시오. 멤버십 전용 방송처럼 서버가 받을 수 없는 것을 위한 길입니다.",
  },
  "jobs.source.hint.url": {
    en: "The server takes the audio itself with yt-dlp. An absolute path to a local file can be pasted in too.",
    ko: "서버가 yt-dlp로 오디오를 직접 받습니다. 로컬 파일의 절대 경로를 붙여 넣어도 됩니다.",
  },
  "jobs.source.remote": {
    en: "{n} lines translated remotely",
    ko: "원격 번역 {n}건",
  },
  "jobs.source.remoteAndLocal": {
    en: "{n} lines translated remotely · {local} fell back to local",
    ko: "원격 번역 {n}건 · 로컬 대체 {local}건",
  },
  "jobs.translating": {
    en: "Translating again…",
    ko: "다시 번역하는 중…",
  },
  "jobs.uploading": {
    en: "Uploading the file · {name}",
    ko: "파일 올리는 중 · {name}",
  },

  /* library.js -- the video and stream list */
  "library.action.delete.session.tip": {
    en: "Deletes this stream's subtitle log",
    ko: "이 방송의 자막 내역을 삭제합니다",
  },
  "library.action.delete.video.tip": {
    en: "Deletes this transcription",
    ko: "이 전사를 삭제합니다",
  },
  "library.action.resume.tip": {
    en: "Resume — appends to the same session",
    ko: "이어받기 — 같은 세션에 이어서 받습니다",
  },
  "library.action.retranscribe.tip": {
    en: "Re-transcribe — the same engine works too. Lines whose text is unchanged keep their translation",
    ko: "다시 전사 — 같은 엔진으로도 됩니다. 글자가 같은 줄의 번역은 남습니다",
  },
  "library.action.whole.tip": {
    en: "Transcribe the whole video — re-transcribes the VOD of a finished stream from end to end",
    ko: "전체 영상 전사 — 끝난 방송의 녹화본을 통째로 다시 전사합니다",
  },
  "library.empty": {
    en: "Nothing yet. Put a URL in with “＋ Add”.",
    ko: "아직 없습니다. 「＋ 추가」로 주소를 넣으십시오.",
  },
  "library.nowTitle.empty": {
    en: "Pick a video, or add one",
    ko: "영상을 고르거나 추가하십시오",
  },
  "library.row.lines": {
    en: "{n} lines",
    ko: "{n}줄",
  },
  "library.row.linesState": {
    en: "{n} lines  ·  {state}",
    ko: "{n}줄  ·  {state}",
  },
  "library.row.minutes": {
    en: "{n} min",
    ko: "{n}분",
  },
  "library.session.delete.confirm": {
    en: "Delete the subtitle log of the stream “{title}”?\nThe subtitles transcribed go with it.",
    ko: "'{title}' 방송의 자막 내역을 삭제할까요?\n받아 적은 자막이 함께 지워집니다.",
  },
  "library.video.delete.confirm": {
    en: "Delete the transcription of “{title}”?\nThe audio taken for it goes with it.",
    ko: "'{title}' 전사를 삭제할까요?\n내려받은 오디오도 함께 지웁니다.",
  },

  /* update.js -- the notification strip and the Update section */
  "update.apply.confirm": {
    en: "Swap in the new version and restart?\nA stream being received is closed properly first.",
    ko: "새 판으로 갈아 끼우고 다시 시작할까요?\n받는 중인 방송은 먼저 제대로 닫힙니다.",
  },
  "update.applying": {
    en: "Restarting on the new version… this screen opens again once it is up.",
    ko: "새 판으로 다시 시작하는 중입니다… 켜지면 이 화면이 다시 열립니다.",
  },
  "update.available": {
    en: "A new version {tag} is out.",
    ko: "새 판 {tag}이 나왔습니다.",
  },
  "update.available.repo": {
    en: "A new version {tag} is out — from the repository, get it with git pull.",
    ko: "새 판 {tag}이 나왔습니다 — 저장소에서는 git pull 로 받으십시오.",
  },
  "update.checking": {
    en: "Checking…",
    ko: "확인하는 중…",
  },
  "update.downloading": {
    en: "Taking the new version {tag}…",
    ko: "새 판 {tag} 받는 중…",
  },
  "update.downloading.pct": {
    en: "Taking the new version {tag}… {pct}%",
    ko: "새 판 {tag} 받는 중… {pct}%",
  },
  "update.failed": {
    en: "Taking the new version failed: {error}",
    ko: "새 판 받기 실패: {error}",
  },
  "update.hint.available": {
    en: "a new version is out",
    ko: "새 판이 있습니다",
  },
  "update.hint.available.repo": {
    en: "a new version is out (git pull from the repository)",
    ko: "새 판이 있습니다 (저장소에서는 git pull)",
  },
  "update.hint.current": {
    en: "Now {version}",
    ko: "지금 {version}",
  },
  "update.hint.latest": {
    en: "latest {tag}",
    ko: "최신 {tag}",
  },
  "update.hint.uptodate": {
    en: "up to date",
    ko: "최신입니다",
  },
  "update.ready": {
    en: "The new version {tag} is taken. Restart and it is applied.",
    ko: "새 판 {tag}을 받아 두었습니다. 다시 시작하면 적용됩니다.",
  },
});
