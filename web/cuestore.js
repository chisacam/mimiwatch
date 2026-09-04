/* In-memory store for live subtitles. One copy shared by the page and the
 * extension.
 *
 * The rules for folding the three events that arrive over SSE -- cue,
 * translation and drop -- into the subtitle list are the same wherever they
 * arrive from.
 *
 *   - A cue that arrives again under the same id is swapped in **in place**,
 *     because a refined line inherits the id of the first final line it
 *     absorbed.
 *   - Lines named in `replaces` drop out of the list. Those are the final lines
 *     the refined line swallowed.
 *   - A translation attaches only when its cue is there. A translation for an
 *     id we do not know is thrown away.
 *
 * These rules existed in three copies -- the mimiwatch page (app/live.js), the
 * extension's content script, and the panel in the chat column -- and a fix in
 * one never reached the others. As with overlay.js, one copy lives here and is
 * copied into the extension folder (bench/ext_check.py compares them).
 *
 * The array is edited **in place**, so that a caller can grab `store.cues` once
 * and keep using it -- the page holds that array as state.cues, and the overlay
 * reads it every frame.
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

      /* Folds one cue event from the server in. The lines the refined line
       * swallowed come out first, then this line goes in or is swapped. The
       * order matters -- the id the refined line inherited is in replaces as
       * well, and taking that one out too would erase the line itself. */
      upsert(m) {
        const removed = [];
        (m.replaces || []).forEach((id) => {
          if (id === m.id) return;
          if (remove(id)) removed.push(id);
        });
        const old = byId.get(m.id);
        const cue = old || { id: m.id, translations: {} };
        // The time arrives as `t` (the stored column is named start, but the
        // wire format keeps its own). `end` is 0 on live -- it means "unknown",
        // and the overlay invents 6 s for it.
        Object.assign(cue, {
          start: m.t, t: m.t, end: m.end || 0, text: m.text,
          lang: m.lang || "", kind: m.kind || "final", speaker: m.speaker || "",
          edited: m.edited || "", arrived: Date.now(),
        });
        if (!old) {                     // SSE arrives in order. No sorting needed.
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

      /* Swaps in a list received in one go, as a VOD's is. The array itself
       * stays the same one. */
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
