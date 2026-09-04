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
  const findColumn = () => document.querySelector("#secondary-inner") ||
                           document.querySelector("#secondary");
  const findChat = () => document.querySelector("ytd-live-chat-frame#chat") ||
                         document.querySelector("#chat");

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
  let rows = new Map();          // cue id → row element
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
    let row = rows.get(c.id);
    if (!row) {
      row = document.createElement("div");
      row.className = "mw-line";
      const body = document.createElement("div");
      body.append(el("div", "mw-tx"), el("div", "mw-tr"));
      row.append(el("div", "mw-t"), body);
      // Press a line in the log and it goes to that point. Here the real <video>
      // can be grabbed, so it just works -- something the script panel on the
      // mimiwatch page cannot do, having no video to attach to.
      row.addEventListener("click", () => {
        if (onSeek) onSeek((c.start != null ? c.start : c.t) || 0);
      });
      rows.set(c.id, row);
      list.appendChild(row);
    }
    return row;
  }

  /* Draws the subtitles. It does not redraw the lot but touches only the lines
   * that changed -- live brings a line every few seconds, and rebuilding
   * hundreds of lines each time shakes the reading position. */
  function render(cues, opts) {
    if (!node) return;
    const trKey = (opts && opts.trKey) || "_";
    const alive = new Set();
    for (const c of cues) {
      alive.add(c.id);
      const row = rowFor(c);
      const t = row.querySelector(".mw-t");
      const tx = row.querySelector(".mw-tx");
      const tr = row.querySelector(".mw-tr");
      const time = fmt((c.start != null ? c.start : c.t) || 0);
      const trText = (c.translations && c.translations[trKey]) || "";
      if (t.textContent !== time) t.textContent = time;
      if (tx.textContent !== c.text) tx.textContent = c.text;
      if (tr.textContent !== trText) tr.textContent = trText;
      row.classList.toggle("mw-note", c.kind === "note");
    }
    // Lines a refined line absorbed, and lines dropped.
    for (const [id, row] of rows) {
      if (!alive.has(id)) { row.remove(); rows.delete(id); }
    }
    shown = cues.length;
    head.querySelector(".mw-count").textContent = t("extpanel.count", { n: shown });
    pin();
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
      if (list) list.textContent = "";
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
