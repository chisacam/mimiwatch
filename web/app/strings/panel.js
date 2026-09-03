/* Strings for the panel area. Filled by the i18n pass. */

/* A reading that varies by a count or by an optional clause gets one key per
 * whole reading, never a stem plus a fragment. Korean puts the count in front
 * of the counter word and English behind the noun, so a sentence glued from
 * halves comes out wrong in one language or the other; that is why the
 * re-translation report carries four keys instead of a base and two suffixes,
 * and why the export hint repeats the format sentence in its live variant.
 *
 * `panel.needTarget` is what the export dialog says too. One sentence in three
 * places should not be able to drift into three wordings. */
MW_I18N.add({
  "export.hint.json": {
    en: "Times, source and translation as they are. Missing-segment notes are kept too.",
    ko: "시각·원문·번역을 그대로 담습니다. 못 받은 구간 안내도 남습니다.",
  },
  "export.hint.srt": {
    en: "A subtitle track. Missing-segment notes are dropped.",
    ko: "자막 트랙입니다. 못 받은 구간 안내는 빠집니다.",
  },
  "export.hint.srt.live": {
    en: "A subtitle track. Missing-segment notes are dropped. Live subtitles have no end time, so it runs to the next subtitle, 6 seconds at most.",
    ko: "자막 트랙입니다. 못 받은 구간 안내는 빠집니다. 라이브 자막에는 끝 시각이 없어 다음 자막까지로 잡고, 최대 6초에서 끊습니다.",
  },
  "export.hint.txt": {
    en: "A record for reading. Missing-segment notes are kept as they are.",
    ko: "읽는 기록입니다. 못 받은 구간 안내도 그대로 남습니다.",
  },
  "export.hint.vtt": {
    en: "A subtitle track. Missing-segment notes are dropped.",
    ko: "자막 트랙입니다. 못 받은 구간 안내는 빠집니다.",
  },
  "export.hint.vtt.live": {
    en: "A subtitle track. Missing-segment notes are dropped. Live subtitles have no end time, so it runs to the next subtitle, 6 seconds at most.",
    ko: "자막 트랙입니다. 못 받은 구간 안내는 빠집니다. 라이브 자막에는 끝 시각이 없어 다음 자막까지로 잡고, 최대 6초에서 끊습니다.",
  },
  "export.what": { en: "{title} — {n} lines", ko: "{title} — {n}줄" },
  "export.what.live": { en: "{title} — {n} lines (live)", ko: "{title} — {n}줄 (라이브)" },
  "panel.edit.at.title": {
    en: "The time (in seconds) this line appears",
    ko: "이 줄이 뜨는 시각(초)",
  },
  "panel.edit.cancel": { en: "Cancel", ko: "취소" },
  "panel.edit.delete.title": { en: "Delete this line", ko: "이 줄을 지웁니다" },
  "panel.edit.save": { en: "Save", ko: "저장" },
  "panel.edit.source": { en: "Source", ko: "원문" },
  "panel.edit.translation": {
    en: "Translation (leave it empty to keep the current one)",
    ko: "번역 (비우면 그대로 둡니다)",
  },
  "panel.needTarget": {
    en: "Open a video or a stream first.",
    ko: "먼저 영상이나 방송을 여십시오.",
  },
  "panel.new.translation": {
    en: "Translation (optional — writing one marks the line hand-edited, so a re-translation will not overwrite it)",
    ko: "번역 (선택 — 쓰면 손편집으로 남아 재번역이 덮지 않습니다)",
  },
  "panel.pick.count": { en: "{n} lines selected", ko: "{n}줄 선택" },
  "panel.pick.countKept": {
    en: "{n} lines selected ({kept} hand-edited lines will be skipped)",
    ko: "{n}줄 선택 (손으로 고친 {kept}줄은 건너뜁니다)",
  },
  "panel.pick.none": { en: "No lines selected", ko: "고른 줄 없음" },
  "panel.retranslate.done": {
    en: "{done} lines translated again",
    ko: "{done}줄 다시 번역했습니다",
  },
  "panel.retranslate.doneEmpty": {
    en: "{done} lines translated again · {skipped} lines had nothing to translate",
    ko: "{done}줄 다시 번역했습니다 · 옮길 것 없음 {skipped}줄",
  },
  "panel.retranslate.doneKept": {
    en: "{done} lines translated again · {kept} hand-edited lines skipped",
    ko: "{done}줄 다시 번역했습니다 · 건너뜀 {kept}줄(손으로 고침)",
  },
  "panel.retranslate.doneKeptEmpty": {
    en: "{done} lines translated again · {kept} hand-edited lines skipped · {skipped} lines had nothing to translate",
    ko: "{done}줄 다시 번역했습니다 · 건너뜀 {kept}줄(손으로 고침) · 옮길 것 없음 {skipped}줄",
  },
  "panel.retranslate.progress": { en: "{done}/{total}", ko: "{done}/{total}" },
  "panel.retranslate.progressKept": {
    en: "{done}/{total} · {kept} hand-edited lines skipped",
    ko: "{done}/{total} · 건너뜀 {kept}줄(손으로 고침)",
  },
  "panel.retranslate.working": { en: "Translating again…", ko: "다시 번역하는 중…" },
  "panel.row.edit.title": { en: "Edit this line", ko: "이 줄을 고칩니다" },
  "panel.row.stale": { en: "⟲ Differs from source", ko: "⟲ 원문과 다름" },
  "panel.row.stale.title": {
    en: "The source was edited, so this translation is of the old sentence",
    ko: "원문을 고친 뒤라 이 번역은 옛 문장의 것입니다",
  },
  "panel.window.title": { en: "Subtitle log", ko: "자막 내역" },
  "panel.window.waiting": {
    en: "Pick the tab to share and the subtitles pile up here.",
    ko: "공유할 탭을 고르면 여기에 자막이 쌓입니다.",
  },
});
