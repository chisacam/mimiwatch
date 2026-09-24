/* Subtitles laid over the video. One copy shared by the page and the extension.
 *
 * Why it was pulled out. The browser extension lays subtitles on the YouTube
 * page **itself**, not over our page's iframe. That puts the code that draws
 * subtitles in two places, and this repository has already paid for that more
 * than once -- the code that drew script lines existed twice, so the speaker
 * label was on only one of them, and the tab-session notice was drawn in one
 * place only, so it vanished on resume. Subtitle look, position and dragging
 * will go on being worked on, so they are one copy from the start.
 *
 * All this module knows about is the cues and the box. It knows nothing of the
 * library, the script panel, SSE or the engine settings. Those are the caller's
 * business.
 *
 *     const ov = MimiOverlay.attach({ overlay, box });
 *     ov.setData({ cues, backend, live, receiving, speakers });
 *     ov.setView({ mode: "both", showPrev: true });
 *     ov.render(currentTime);        // -> index of the current line, -1 if none
 *
 * The browser just reads it with a <script>. The extension's content script
 * reads the same file, so it uses no module syntax.
 */
(function (root) {
  "use strict";

  /* The subtitle position is written down as a **ratio of the box**, not in
   * pixels. A window and full screen are different sizes and the window itself
   * grows and shrinks, so written in pixels the position chosen in the window
   * ends up in the top-left corner in full screen.
   *   x -- horizontal ratio of the **centre** of the subtitle block, measured
   *        from the left of the box
   *   y -- vertical ratio of the bottom edge of the subtitle block, measured
   *        from the **bottom** of the box
   * y is measured from the bottom rather than the top because the bottom is the
   * side the subtitle has to stay stuck to. Raise the font size and the bottom
   * edge stays where it was. */
  const DEFAULT_POS = { x: 0.5, y: 0.06 };

  /* A subtitle line is held this much longer after its own end time passes. A
   * subtitle that blinks away during the natural pause after a sentence reads
   * as one that was missed. */
  const HOLD_S = 1.2;
  /* The limit on holding the last line while receiving. Older than this and it
   * comes down -- an old subtitle left on screen after the stream has gone
   * quiet looks like something just said. */
  const LIVE_STALE_S = 20;

  function attach(opts) {
    const overlay = opts.overlay;
    const boxOf = typeof opts.box === "function" ? opts.box : () => opts.box;
    const el = {
      prev: overlay.querySelector(".cue-prev"),
      main: overlay.querySelector(".cue-main"),
      src: overlay.querySelector(".cue-src"),
    };

    const st = {
      cues: [], backend: "", live: false, receiving: false, speakers: false,
      mode: "both", showPrev: true,
      idx: -1, px: 30, scale: 1, pos: Object.assign({}, DEFAULT_POS),
      // What the three lines hold now, as one string. Both callers draw ten
      // times a second, and every draw used to rebuild all three lines and
      // then read the block's size back to clamp it -- a layout forced on the
      // host page ten times a second while the words on screen stayed the
      // same. A draw that would put back what is already there is skipped.
      // Compared as text rather than by cue: a refined line replaces its
      // final under the same id and a translation lands on the same object,
      // both in place, so only the text tells that something changed.
      drawn: null,
    };
    let drag = null;
    const api = {};

    /* ---------- which line ---------- */

    /* Cues are sorted and do not overlap, so walking beats a binary search --
     * playback moves about 0.1 s at a time and almost always lands on the same
     * line or the very next one. */
    api.cueAt = function (t) {
      const c = st.cues;
      if (!c.length) return -1;

      // Live subtitles cannot be looked up the way a VOD's are. A line takes
      // several seconds to come out, so by the time it exists the player is
      // already past that time. Matching on time would always show nothing. So
      // the most recently recognized line is held until the next one arrives.
      // That is how live subtitles are read anyway. The offset still shifts the
      // whole track.
      //
      // This rule holds **only while receiving**. Once the stream is over and it
      // becomes a VOD, the time-based lookup below takes over -- without that,
      // no subtitle would show at any position from 20 s after the last line.
      if (st.live && st.receiving) {
        const i = c.length - 1;
        const age = (Date.now() - (c[i].arrived || 0)) / 1000;
        return age > LIVE_STALE_S ? -1 : i;
      }

      let i = st.idx >= 0 && st.idx < c.length ? st.idx : 0;
      while (i > 0 && startOf(c[i]) > t) i--;
      while (i < c.length - 1 && startOf(c[i + 1]) <= t) i++;
      if (t < startOf(c[i])) return -1;
      if (t > endOf(c[i]) + HOLD_S) return -1;
      return i;
    };

    const startOf = (c) => (c.start != null ? c.start : c.t) || 0;
    const endOf = (c) => (c.end != null && c.end > startOf(c)
                          ? c.end : startOf(c) + 6);

    /* ---------- drawing ---------- */

    const trOf = (c) => (c && c.translations ? c.translations[st.backend] : null);
    const chipFor = (c) => (st.speakers && c && c.speaker) ? c.speaker : "";

    // A line with no translation (a fragment the model would only ruin) belongs
    // on the source side. Shown inside "Translation only" or "Both" it reads as
    // a line whose translation failed, rather than one that never needed one.
    function lineOf(c) {
      if (!c) return "";
      if (st.mode === "source") return c.text;
      if (st.mode === "off") return "";
      return trOf(c) || "";
    }

    function put(node, text, speaker) {
      if (!node) return;
      node.textContent = "";
      if (!text) return;
      const s = document.createElement("span");
      if (speaker) {
        const chip = document.createElement("b");
        chip.className = "spk-chip";
        chip.textContent = speaker;
        s.appendChild(chip);
      }
      s.appendChild(document.createTextNode(text));
      node.appendChild(s);
    }

    /* `t` is the playback position with the offset already added. What comes
     * back is the index of the current line, or -1 if there is none. Whether it
     * changed is the caller's to know. */
    api.render = function (t) {
      const i = api.cueAt(t);
      const cur = i >= 0 ? st.cues[i] : null;
      const prev = i > 0 ? st.cues[i - 1] : null;
      const main = lineOf(cur), mainChip = chipFor(cur);
      const src = st.mode === "both" && trOf(cur) ? cur.text : "";
      const before = st.showPrev ? lineOf(prev) : "", beforeChip = chipFor(prev);
      const key = [main, mainChip, src, before, beforeChip].join("\u0000");
      st.idx = i;
      if (key === st.drawn) return i;
      st.drawn = key;
      watchBox();
      put(el.main, main, mainChip);
      put(el.src, src);
      put(el.prev, before, beforeChip);
      // When the sentence changes so does the size of the block. So that a
      // subtitle placed where a short sentence sat does not run outside the box
      // on a long one, the position is clamped again on every new sentence.
      // A box that changes size is the caller's to report -- setSize and
      // setPos both reflow.
      api.reflow();
      return i;
    };

    api.clear = function () {
      put(el.main, ""); put(el.src, ""); put(el.prev, "");
      st.idx = -1;
      // Forgotten with the text, or the next draw of the same line would
      // think it is still on screen and leave the overlay blank.
      st.drawn = null;
    };

    /* ---------- putting values in ---------- */

    api.setData = function (d) {
      // Both callers pass the cue array on every draw, and this used to throw
      // the index away every time, so the walk in cueAt started from line 0
      // ten times a second -- 97 us a draw at 6,000 cues, against 0.3 us with
      // the index kept. The stores edit their array in place (cuestore.js),
      // so the same array is the same list, grown or trimmed; the walk steps
      // from any index to the right line in a sorted list, and cueAt bounds
      // the index, so it is kept. A different array is a different video.
      if (d.cues && d.cues !== st.cues) { st.cues = d.cues; st.idx = -1; }
      if (d.backend !== undefined) st.backend = d.backend;
      if (d.live !== undefined) st.live = !!d.live;
      if (d.receiving !== undefined) st.receiving = !!d.receiving;
      if (d.speakers !== undefined) st.speakers = !!d.speakers;
    };

    api.setView = function (v) {
      if (v.mode !== undefined) st.mode = v.mode;
      if (v.showPrev !== undefined) st.showPrev = !!v.showPrev;
    };

    api.index = () => st.idx;
    api.resetIndex = () => { st.idx = -1; };

    /* The font size is not the chosen value as it is but grown by **how much
     * bigger the box got**. The 30px chosen in a window, used unchanged in full
     * screen, makes the subtitle alone look smaller by however many times the
     * screen grew. The caller decides the scale -- what to measure it against
     * differs between the page and the extension. */
    api.setSize = function (px, scale) {
      if (px != null) st.px = px;
      if (scale != null) st.scale = scale;
      const eff = Math.round(st.px * (st.scale || 1));
      overlay.style.fontSize = eff + "px";
      if (el.main) el.main.style.fontSize = eff + "px";
      api.reflow();
      return eff;
    };

    /* ---------- position ---------- */

    api.pos = () => Object.assign({}, st.pos);

    api.setPos = function (p) {
      st.pos = { x: p && p.x != null ? p.x : DEFAULT_POS.x,
                 y: p && p.y != null ? p.y : DEFAULT_POS.y };
      api.reflow();
    };

    api.resetPos = function () {
      st.pos = Object.assign({}, DEFAULT_POS);
      api.reflow();
      if (api.onPos) api.onPos(api.pos());
    };

    /* A box that changes size with no window resize behind it -- YouTube's
     * theater mode, the page's script panel closing, a multiview relayout --
     * used to be caught by the clamp that ran on every draw. Draws that change
     * nothing are skipped now, so the box is watched instead: the observer
     * reads sizes after the browser has laid the page out anyway, rather than
     * forcing a layout of its own. Where there is no ResizeObserver (tests
     * under node) nothing is watched. */
    let ro = null, watched = null;
    function watchBox() {
      if (typeof ResizeObserver === "undefined") return;
      const b = boxOf();
      if (b === watched) return;
      if (!ro) ro = new ResizeObserver(() => api.reflow());
      if (watched) ro.unobserve(watched);
      watched = b || null;
      if (watched) ro.observe(watched);
    }

    /* Seats the stored ratios as an actual left/bottom. */
    api.reflow = function () {
      const p = clamp(st.pos);
      overlay.style.left = (p.x * 100).toFixed(3) + "%";
      overlay.style.bottom = (p.y * 100).toFixed(3) + "%";
    };

    /* The stored value is not used as it is; it is clamped once more on every
     * draw.
     *
     * The size of the subtitle block changes every time with the font size and
     * with whatever sentence came out. It may have been inside the box when it
     * was placed and still stick out and have its text cut off on the next
     * longer sentence. Only the **displayed value** is clamped -- shrinking the
     * stored value along with it would drag the position the user chose a
     * little further inward every time a short sentence goes past. */
    function clamp(p) {
      const box = boxOf();
      if (!box) return p;
      const bw = box.clientWidth, bh = box.clientHeight;
      if (!bw || !bh) return p;
      // The centre is the anchor, so the margin on each side is half the width
      // of the block. When the subtitle is wider than the box (a very large
      // font plus a long sentence) there is no margin, so it goes in the centre.
      const half = Math.min(overlay.offsetWidth / 2 / bw, 0.5);
      const top = Math.max(0, 1 - overlay.offsetHeight / bh);
      return {
        x: Math.min(Math.max(p.x, half), 1 - half),
        y: Math.min(Math.max(p.y, 0), top),
      };
    }
    api.clamp = clamp;

    /* ---------- dragging ----------
     *
     * setPointerCapture is required. The moment the pointer leaves the subtitle
     * -- that is, moves over the video -- that movement becomes that document's
     * and never reaches us, and across origins a mousemove bound on document
     * does not come through either. With capture set, every move comes to this
     * element until release. Handling mouse and touch with one set of code is a
     * bonus. */
    function down(e) {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const r = overlay.getBoundingClientRect();
      drag = {
        id: e.pointerId,
        // The gap between the point grabbed and the anchor (centre-bottom).
        // Without subtracting it, the subtitle jerks under the cursor the
        // instant it is pressed.
        dx: e.clientX - (r.left + r.width / 2),
        dy: e.clientY - r.bottom,
      };
      overlay.setPointerCapture(e.pointerId);
      overlay.classList.add("dragging");
      e.preventDefault();
    }

    function move(e) {
      if (!drag || e.pointerId !== drag.id) return;
      // This works unchanged in full screen too. clientX/Y and this rectangle
      // are both viewport-based, so when the box becomes the whole screen the
      // two values grow together.
      const box = boxOf();
      if (!box) return;
      const r = box.getBoundingClientRect();
      if (!r.width || !r.height) return;
      st.pos = {
        x: (e.clientX - drag.dx - r.left) / r.width,
        y: (r.bottom - (e.clientY - drag.dy)) / r.height,
      };
      api.reflow();
    }

    function up(e) {
      if (!drag || e.pointerId !== drag.id) return;
      overlay.classList.remove("dragging");
      drag = null;
      // The released position is stored **clamped**. Letting go far outside the
      // box and keeping that value as it is would revive an off-screen position
      // the next time it opens.
      st.pos = clamp(st.pos);
      if (api.onPos) api.onPos(api.pos());
    }

    overlay.addEventListener("pointerdown", down);
    overlay.addEventListener("pointermove", move);
    overlay.addEventListener("pointerup", up);
    overlay.addEventListener("pointercancel", up);

    api.destroy = function () {
      if (ro) { ro.disconnect(); ro = null; watched = null; }
      overlay.removeEventListener("pointerdown", down);
      overlay.removeEventListener("pointermove", move);
      overlay.removeEventListener("pointerup", up);
      overlay.removeEventListener("pointercancel", up);
    };

    return api;
  }

  root.MimiOverlay = { attach, DEFAULT_POS, HOLD_S, LIVE_STALE_S };
})(typeof window !== "undefined" ? window : globalThis);
