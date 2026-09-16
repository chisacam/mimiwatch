/* Strings for the document view. Filled by the i18n pass. */
MW_I18N.add({
  "outline.toggle": { en: "Document", ko: "문서뷰" },
  "outline.toggle.title": {
    en: "Show the running summary over the video area",
    ko: "영상 자리에 지금까지의 요약을 띄웁니다",
  },
  "outline.head": { en: "Document", ko: "문서" },
  "outline.close": { en: "Close", ko: "닫기" },
  "outline.download": { en: "Save as Markdown", ko: "마크다운으로 저장" },
  "outline.stats": {
    en: "{sections} sections · {lines} lines",
    ko: "{sections}개 절 · {lines}줄",
  },
  "outline.writing": { en: "writing", ko: "쓰는 중" },

  /* Off, but available. */
  "outline.off.title": { en: "The document is not being written", ko: "문서를 쓰고 있지 않습니다" },
  "outline.off.body": {
    en: "Each pass is one generation on the same engine the subtitles use, so it runs only when you ask for it.",
    ko: "한 번 쓸 때마다 자막과 같은 엔진을 한 번씩 쓰기 때문에, 요청할 때만 돕니다.",
  },
  "outline.start": { en: "Start writing", ko: "문서 쓰기 시작" },
  "outline.stop": { en: "Stop writing", ko: "문서 쓰기 멈춤" },

  /* On, but nothing written yet. */
  "outline.waiting.title": { en: "Listening", ko: "듣고 있습니다" },
  "outline.waiting.body": {
    en: "The first section is written once about a minute of speech has come in.",
    ko: "말이 1분쯤 쌓이면 첫 절을 씁니다.",
  },

  /* The engine cannot write prose at all. */
  "outline.blocked.title": { en: "This engine cannot write a summary", ko: "이 엔진은 요약을 쓰지 못합니다" },
  "outline.blocked.body": {
    en: "M2M-100 translates a sentence into another language and does nothing else. Pick Gemma or an OpenAI-compatible engine to write the document.",
    ko: "M2M-100은 문장을 다른 말로 옮길 뿐, 그 밖의 일은 하지 못합니다. 문서를 쓰려면 Gemma나 OpenAI 호환 엔진을 고르세요.",
  },
  "outline.blocked.action": { en: "Open engine settings", ko: "엔진 설정 열기" },

  /* A recording, or a session that has finished. */
  "outline.rebuild": { en: "Write the document", ko: "문서 쓰기" },
  "outline.rebuild.again": { en: "Write it again", ko: "다시 쓰기" },
  "outline.rebuild.running": { en: "Writing… {done}/{total}", ko: "쓰는 중… {done}/{total}" },
  "outline.rebuild.body": {
    en: "This recording is finished, so the whole transcript is read in one go rather than as it arrives.",
    ko: "끝난 녹화본이라 도착하는 대로가 아니라 전사본 전체를 한 번에 읽습니다.",
  },
  "outline.empty.body": {
    en: "There are no subtitles to read yet.",
    ko: "아직 읽을 자막이 없습니다.",
  },

  "outline.error": { en: "The last pass failed: {reason}", ko: "마지막 쓰기가 실패했습니다: {reason}" },
  "outline.error.kept": {
    en: "What is above was written before it and is left as it was.",
    ko: "위의 내용은 그 전에 쓰인 것이고 그대로 두었습니다.",
  },
});
