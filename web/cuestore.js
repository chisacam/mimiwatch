/* 라이브 자막의 메모리 저장소. 페이지와 확장이 함께 쓰는 한 벌입니다.
 *
 * SSE로 오는 이벤트 세 가지 -- 자막(cue), 번역(translation), 지움(drop) -- 을
 * 자막 목록에 반영하는 규칙은 어디서 받든 같습니다.
 *
 *   - 같은 id의 자막이 다시 오면 **그 자리에서** 갈아 끼웁니다. 정제본이
 *     자기가 흡수한 첫 확정 줄의 id를 물려받기 때문입니다.
 *   - `replaces`에 적힌 줄은 목록에서 빠집니다. 정제본이 삼킨 확정 줄들입니다.
 *   - 번역은 자막이 있을 때만 붙습니다. 모르는 id의 번역은 버립니다.
 *
 * 이 규칙이 mimiwatch 페이지(app/live.js), 확장의 content script, 채팅 자리
 * 패널에 세 벌 있었고, 한쪽에서 고친 것이 다른 쪽에 닿지 않았습니다.
 * overlay.js와 같은 방식으로 한 벌만 두고 확장 폴더에 복사합니다
 * (bench/ext_check.py가 사본을 맞댑니다).
 *
 * 배열은 **제자리에서** 고칩니다. 부르는 쪽이 `store.cues`를 한 번 잡아 두고
 * 계속 쓸 수 있게요 -- 페이지는 그 배열을 state.cues로 들고, 오버레이는 그것을
 * 매 프레임 읽습니다.
 *
 *     const store = MimiCues.create();
 *     const r = store.upsert(event);      // { cue, isNew, removed: [id, ...] }
 *     store.translate(id, key, text);     // -> cue | null
 *     store.drop(id);                     // -> cue | null
 *     store.load(list); store.reset();
 */
(function (root) {
  "use strict";

  function create() {
    const cues = [];
    const byId = new Map();

    function remove(id) {
      const c = byId.get(id);
      if (!c) return null;
      const i = cues.indexOf(c);
      if (i >= 0) cues.splice(i, 1);
      byId.delete(id);
      return c;
    }

    const api = {
      cues,
      get: (id) => byId.get(id) || null,
      size: () => cues.length,

      /* 서버가 보낸 cue 이벤트 하나를 반영합니다. 정제본이 삼킨 줄은 먼저
       * 빼고, 그 다음 이 줄을 넣거나 갈아 끼웁니다. 순서가 중요합니다 --
       * 정제본이 물려받은 id는 replaces에도 들어 있는데, 그것까지 빼면
       * 자기 자신을 지우게 됩니다. */
      upsert(m) {
        const removed = [];
        (m.replaces || []).forEach((id) => {
          if (id === m.id) return;
          if (remove(id)) removed.push(id);
        });
        const old = byId.get(m.id);
        const cue = old || { id: m.id, translations: {} };
        // 시각은 `t`로 옵니다(저장 열 이름은 start이지만 통신 형식은 그대로).
        // `end`는 라이브에서 0 -- 「모른다」는 뜻이고, 오버레이가 6초로 짓습니다.
        Object.assign(cue, {
          start: m.t, t: m.t, end: m.end || 0, text: m.text,
          lang: m.lang || "", kind: m.kind || "final", speaker: m.speaker || "",
          edited: m.edited || "", arrived: Date.now(),
        });
        if (!old) {                     // SSE는 순서대로 옵니다. 정렬은 필요 없습니다.
          cues.push(cue);
          byId.set(m.id, cue);
        }
        return { cue, isNew: !old, removed };
      },

      translate(id, key, text) {
        const c = byId.get(id);
        if (!c) return null;
        c.translations[key] = text;
        return c;
      },

      drop: remove,

      /* 녹화본처럼 한 번에 받은 목록으로 갈아 끼웁니다. 배열 정체는 그대로. */
      load(list) {
        cues.length = 0;
        byId.clear();
        for (const c of list || []) {
          if (!c.translations) c.translations = {};
          cues.push(c);
          if (c.id != null) byId.set(c.id, c);
        }
      },

      reset() { api.load([]); },
    };
    return api;
  }

  root.MimiCues = { create };
})(typeof window !== "undefined" ? window : globalThis);
