/* Puts the subtitle log up in the chat column.
 *
 * **Why mimiwatch's script panel was not framed in.** That was the plan -- using
 * the `?script=…` screen already built means not one line to write anew.
 * YouTube's CSP has neither frame-src nor default-src, so it does not block it;
 * what does is an https page framing http, blocked as mixed content. Both
 * `127.0.0.1` and `localhost` stayed at about:blank. So it is drawn here
 * directly.
 *
 * It is read-only. Editing, retranslating and exporting are on the mimiwatch
 * page -- bringing them over here would make two copies, and what is needed in
 * this spot is reading.
 */
(function (root) {
  "use strict";

  const ID = "mimiwatch-panel";
  // YouTube's right-hand column. On live it holds the chat, otherwise the
  // related videos.
  // Which column and which chat is the site table's business (ytid.js). A site
  // that names neither has no place to put the log, and mount() says so rather
  // than guessing at a container and hiding something the reader wanted.
  const siteHere = () => MimiYtId.siteOf(location.href);
  const findColumn = () => {
    const s = siteHere();
    return s && s.column ? document.querySelector(s.column) : null;
  };
  const findChat = () => {
    const s = siteHere();
    return s && s.chat ? document.querySelector(s.chat) : null;
  };

  /* A small helper. createElement + className + textContent in one line. */
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  let node = null, list = null, head = null;
  // The things written into the heading. They have to be rewritten when the
  // language changes, and putting the panel back up then would throw the reading
  // position away entirely -- so they are held on to and only the text is swapped.
  let follLabel = null, shown = 0;
  // cue id → { row, t, tx, tr, time, text, trText, note, cue }. The row's
  // three children and what was last written into them are held here, so a
  // pass neither looks them up again nor reads the text back out of the page.
  let rows = new Map();
  // Set by reset() and on mount: the next render goes over every line instead
  // of only the ones it is told changed.
  let whole = true;
  let hidden = null;             // the chat we hid. Used to put it back.
  let onSeek = null;
  let follow = true;

  /* Sweeps up **everything** of ours left in the document.
   *
   * Tracking only one misses them. YouTube swaps the screen out and takes the
   * whole right-hand column off and puts it back, and while it is off
   * `node.isConnected` is false so one more is made; once the old one comes back
   * there are two from then on. Only the new one is tracked, so the old one did
   * not go away even with the checkbox switched off. */
  function sweep() {
    document.querySelectorAll("#" + ID).forEach((e) => e.remove());
  }

  function mount() {
    const col = findColumn();
    if (!col) return false;
    if (node && node.isConnected && node.parentElement === col) return true;
    sweep();

    node = document.createElement("div");
    node.id = ID;
    node.className = "mw-panel";
    head = document.createElement("div");
    head.className = "mw-panel-head";
    // The count starts at 0 on screen, so the one remembered has to as well
    // -- render() writes it only when it differs.
    shown = 0;
    whole = true;
    // It does not use innerHTML. YouTube has Trusted Types switched on
    // (`require-trusted-types-for 'script'`), and putting a string into
    // innerHTML on that document is refused. Whether a content script is exempt
    // depends on the Chrome version, so it does not lean on it at all.
    head.append(el("b", "", t("extpanel.title")),
                el("span", "mw-count", t("extpanel.count", { n: 0 })));
    const foll = el("label", "mw-follow");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = true;
    // The one space between the checkbox and the text. It is layout, not a
    // string, so it stays outside the table.
    follLabel = document.createTextNode(" " + t("extpanel.follow"));
    foll.append(box, follLabel);
    head.appendChild(foll);
    list = document.createElement("div");
    list.className = "mw-panel-list";
    node.append(head, list);

    // If there is a chat, its place is taken instead. Side by side both get
    // narrow and neither can be read.
    const chat = findChat();
    if (chat && chat.style.display !== "none") {
      hidden = chat;
      // It inherits the chat's height. It goes into that spot, so its size has
      // to be that spot's too or it looks out of place.
      const h = chat.getBoundingClientRect().height;
      if (h > 200) node.style.height = Math.round(h) + "px";
      chat.style.display = "none";
    }
    col.insertBefore(node, col.firstChild);

    head.querySelector("input").addEventListener("change", (e) => {
      follow = e.target.checked;
      if (follow) pin();
    });
    // Following stops once the user scrolls up to read. Being dragged down to
    // the bottom mid-read means finding that line again.
    list.addEventListener("scroll", () => {
      const atEnd = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
      if (atEnd !== follow) {
        follow = atEnd;
        head.querySelector("input").checked = atEnd;
      }
    });
    return true;
  }

  function unmount() {
    sweep();
    if (hidden) { hidden.style.display = ""; hidden = null; }
    // Even a chat we did not hide ourselves is put back if it was left hidden
    // because of us. When the screen is swapped out, hidden comes to point at
    // the old element and the real chat is left hidden.
    const chat = findChat();
    if (chat && chat.style.display === "none") chat.style.display = "";
    node = list = head = follLabel = null;
    rows = new Map();
    whole = true;
  }

  /* The language has changed. Only our own strings in the heading are rewritten
   * -- the subtitle text and the times have nothing to do with language, and
   * redrawing shakes the reading position. */
  function relabel() {
    if (!node) return;
    head.querySelector("b").textContent = t("extpanel.title");
    head.querySelector(".mw-count").textContent = t("extpanel.count", { n: shown });
    if (follLabel) follLabel.nodeValue = " " + t("extpanel.follow");
  }
  MW_I18N.onChange(relabel);

  function pin() {
    if (list && follow) list.scrollTop = list.scrollHeight;
  }

  const fmt = (s) => {
    const t = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), x = t % 60;
    const two = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${two(m)}:${two(x)}` : `${two(m)}:${two(x)}`;
  };

  function rowFor(c) {
    let r = rows.get(c.id);
    if (!r) {
      const row = document.createElement("div");
      row.className = "mw-line";
      const body = document.createElement("div");
      r = { row, t: el("div", "mw-t"), tx: el("div", "mw-tx"), tr: el("div", "mw-tr"),
            time: null, text: null, trText: null, note: null, cue: c };
      body.append(r.tx, r.tr);
      row.append(r.t, body);
      // Press a line in the log and it goes to that point. Here the real <video>
      // can be grabbed, so it just works -- something the script panel on the
      // mimiwatch page cannot do, having no video to attach to. The cue is read
      // at the click, since a refined line can move the time under the same id.
      row.addEventListener("click", () => {
        const x = r.cue;
        if (onSeek) onSeek((x.start != null ? x.start : x.t) || 0);
      });
      rows.set(c.id, r);
      list.appendChild(row);
    }
    return r;
  }

  /* Writes one line, touching only what differs from what was written last. */
  function paint(c, trKey) {
    const r = rowFor(c);
    r.cue = c;
    const time = fmt((c.start != null ? c.start : c.t) || 0);
    const trText = (c.translations && c.translations[trKey]) || "";
    const note = c.kind === "note";
    if (r.time !== time) r.t.textContent = r.time = time;
    if (r.text !== c.text) r.tx.textContent = r.text = c.text;
    if (r.trText !== trText) r.tr.textContent = r.trText = trText;
    if (r.note !== note) { r.row.classList.toggle("mw-note", note); r.note = note; }
  }

  function unrow(id) {
    const r = rows.get(id);
    if (r) { r.row.remove(); rows.delete(id); }
  }

  /* Draws the subtitles. It does not redraw the lot but touches only the lines
   * that changed -- live brings a line every few seconds, and rebuilding
   * hundreds of lines each time shakes the reading position.
   *
   * `opts.changed` (id → cue) and `opts.removed` (ids) are what the caller saw
   * change since the last render. Every pass used to go over every line --
   * three querySelector calls and three reads of the text back out of the page
   * per line, then a layout read to pin the scroll -- every 400 ms while lines
   * arrived and on every resize event, however few lines had changed. Given
   * them, only those lines are touched; without them, or after a reset or a
   * mount, every line is. */
  function render(cues, opts) {
    if (!node) return;
    const o = opts || {};
    const trKey = o.trKey || "_";
    let touched = false;
    if (whole || !o.changed) {
      whole = false;
      const alive = new Set();
      for (const c of cues) { alive.add(c.id); paint(c, trKey); }
      // Lines a refined line absorbed, and lines dropped.
      for (const id of [...rows.keys()]) if (!alive.has(id)) unrow(id);
      touched = true;
    } else {
      for (const id of o.removed || []) { if (rows.has(id)) { unrow(id); touched = true; } }
      for (const c of o.changed.values()) { paint(c, trKey); touched = true; }
    }
    if (shown !== cues.length) {
      shown = cues.length;
      head.querySelector(".mw-count").textContent = t("extpanel.count", { n: shown });
    }
    if (touched) pin();
  }

  root.MimiPanel = {
    mount, unmount, render,
    mounted: () => !!(node && node.isConnected),
    setSeek: (fn) => { onSeek = fn; },
    /* Throws away every line held.
     *
     * **It erases them from the screen too.** Emptying only the map has the
     * next render attach the same lines on top of ones already there -- picking
     * a VOD laid one more set of subtitles on and each came out twice. Called on
     * a session change, on a move to a different video, and when YouTube swaps
     * the screen out. */
    reset: () => {
      rows = new Map();
      whole = true;
      if (list) list.textContent = "";
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
