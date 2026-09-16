/* The document view -- the running summary, laid over the video area.
 *
 * A seminar is the case this exists for. The subtitles answer "what is being
 * said now" and the script panel answers "what was said, in order"; neither
 * answers "what has this been about", which is the only question anyone has at
 * the end of a talk. So the same area that holds the video holds a document
 * instead, written forward as the talk goes (outline.py on the server decides
 * what goes in it).
 *
 * It is laid over the player rather than swapped for it, and the player is not
 * paused. For a microphone session there is nothing underneath to begin with;
 * for a talk being watched, the sound is the point and the picture of a slide
 * is not, so reading the document while the speaker keeps talking is the
 * wanted behaviour rather than a compromise.
 *
 * The document is not written unless it is asked for. One pass is a whole
 * generation on the same engine the subtitles are translated with, so a
 * session that nobody has opened the document on pays nothing.
 */

/* ---------- which transcript the document belongs to ---------- */
/* The same id a subtitle is filed under: a session id while live, the video id
 * for a recording. Empty when the tile is showing nothing. */
function outlineOwner() {
  if (state.live && state.live.id) return state.live.id;
  if (state.doc && state.doc.id) return state.doc.id;
  return "";
}

function outlineIsLive() {
  return !!(state.live && state.live.id);
}

/* ---------- showing and hiding ---------- */
function syncDocViewButton() {
  const b = $("doc-view-toggle");
  if (!b) return;
  // The button is offered wherever there is a transcript to read. Which
  // engine is configured decides whether the document can be *written*, and
  // that is said inside the panel rather than by hiding the button -- a
  // control that is simply absent teaches nobody why.
  b.hidden = !outlineOwner();
  b.classList.toggle("on", !!state.docView);
}

function setDocView(on) {
  state.docView = !!on;
  const el = $("doc-view");
  if (el) el.hidden = !state.docView;
  syncDocViewButton();
  persist();
  if (state.docView) {
    renderDocView();
    // What the page has may be a document from before this tab was opened.
    if (outlineOwner() && !state.outline) loadOutline(outlineOwner());
  }
}

function toggleDocView() {
  setDocView(!state.docView);
}

/* ---------- talking to the server ---------- */
async function loadOutline(owner) {
  if (!owner) return;
  try {
    const res = await fetch(`/api/outline/${encodeURIComponent(owner)}`);
    const doc = await res.json();
    // The reply may arrive after the tile moved on to something else. Writing
    // it in then would show one talk's document over another's video.
    if (outlineOwner() !== owner) return;
    state.outline = doc;
  } catch (err) {
    console.error("[outline]", err);
  }
  renderDocView();
}

async function setOutlineRunning(on) {
  const owner = outlineOwner();
  if (!owner) return;
  try {
    const res = await fetch("/api/outline", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: owner, on: !!on }),
    });
    const doc = await res.json();
    if (outlineOwner() === owner && !doc.error) state.outline = doc;
  } catch (err) {
    console.error("[outline]", err);
  }
  renderDocView();
}

async function rebuildOutline() {
  const owner = outlineOwner();
  if (!owner) return;
  try {
    await fetch("/api/outline/rebuild", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: owner }),
    });
  } catch (err) {
    console.error("[outline]", err);
  }
  // What happens next arrives on the change feed (bus.js), the same way a
  // transcription job reports itself.
}

/* The `outline` frame on a session's own stream, and the one the rebuild job
 * publishes to the change feed. Both carry the whole document -- it is one
 * frame every minute or two, so sending only what changed would be a
 * reconciliation problem bought for nothing. */
function onOutlineEvent(m, tile) {
  const live = tile && tile.live;
  if (live && state.live !== live) return;   // a tile that is not on screen
  state.outline = m;
  if (state.docView) renderDocView();
}

/* The rebuild job's report, off the change feed. It carries an owner rather
 * than riding a session stream, because the recording it is writing about has
 * no session -- so the first thing to settle is whether it is about what this
 * screen is looking at.
 *
 * **It carries counters, not the document.** The feed reaches every open page
 * once per window, and a document of two hundred sections is not a
 * notification -- so the frame says which part changed and the page reads that
 * part back, which is the rule the rest of bus.js follows. Writing the frame
 * straight into `state.outline` would put a *number* where the section list
 * belongs. */
function onOutlineJobEvent(m) {
  if (!m.owner || m.owner !== outlineOwner()) return;
  if (state.docView) loadOutline(m.owner);
}

/* ---------- drawing ---------- */
function docNotice(title, body, actions) {
  const box = document.createElement("div");
  box.className = "doc-notice";
  const h = document.createElement("b");
  h.textContent = title;
  const p = document.createElement("p");
  p.textContent = body;
  box.append(h, p);
  for (const a of actions || []) {
    const btn = document.createElement("button");
    btn.className = "seg";
    btn.textContent = a.label;
    btn.onclick = a.onClick;
    box.append(btn);
  }
  return box;
}

function docClock(seconds) {
  if (!seconds || seconds < 0) return "";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? `${two(h)}:${two(m)}:${two(s)}` : `${two(m)}:${two(s)}`;
}

function docSection(sec, last) {
  const box = document.createElement("section");
  box.className = "doc-sec" + (last ? " open" : "");
  const head = document.createElement("h3");
  head.textContent = sec.title || "";
  const stamp = document.createElement("button");
  stamp.className = "doc-at";
  stamp.textContent = docClock(sec.t);
  stamp.hidden = !stamp.textContent;
  // A heading is only worth its place if it leads back to the speech it was
  // written from. For a live session the player is at the live edge and
  // seeking backwards is a move that cannot be taken back, so it is left to
  // the recording and to a stream with a real playhead.
  stamp.onclick = () => {
    if (state.player && !outlineIsLive()) {
      state.player.seekTo(sec.t || 0, true);
      state.player.playVideo();
    }
  };
  if (outlineIsLive()) stamp.disabled = true;
  head.append(stamp);
  box.append(head);
  const ul = document.createElement("ul");
  for (const b of sec.bullets || []) {
    const li = document.createElement("li");
    li.textContent = b;
    ul.append(li);
  }
  box.append(ul);
  if (last) {
    const mark = document.createElement("span");
    mark.className = "doc-writing";
    mark.textContent = t("outline.writing");
    box.append(mark);
  }
  return box;
}

function renderDocView() {
  const el = $("doc-view");
  if (!el || el.hidden) return;
  const body = el.querySelector(".doc-body");
  const meta = el.querySelector(".doc-meta");
  const dl = el.querySelector(".doc-dl");
  const stop = el.querySelector(".doc-stop");
  body.textContent = "";
  const doc = state.outline || {};
  const secs = doc.sections || [];
  const owner = outlineOwner();

  meta.textContent = secs.length
    ? t("outline.stats", { sections: secs.length, lines: doc.lines || 0 })
    : "";
  dl.hidden = !secs.length || !owner;
  if (!dl.hidden) dl.href = `/api/outline/${encodeURIComponent(owner)}?fmt=md`;
  stop.hidden = !(outlineIsLive() && doc.running);
  stop.textContent = t("outline.stop");

  for (const [i, sec] of secs.entries()) {
    body.append(docSection(sec, doc.running && i === secs.length - 1));
  }

  // The notice goes under whatever has been written, not instead of it. A
  // document that stopped growing is still the document, and replacing it with
  // the reason it stopped would throw away the part that was wanted.
  if (doc.can_outline === false) {
    body.append(docNotice(t("outline.blocked.title"), t("outline.blocked.body"),
                          [{ label: t("outline.blocked.action"),
                             onClick: () => openSettings() }]));
  } else if (!owner) {
    body.append(docNotice(t("outline.off.title"), t("outline.empty.body"), []));
  } else if (outlineIsLive() && !doc.running) {
    body.append(docNotice(t("outline.off.title"), t("outline.off.body"),
                          [{ label: t("outline.start"),
                             onClick: () => setOutlineRunning(true) }]));
  } else if (!outlineIsLive()) {
    body.append(docNotice(
      secs.length ? t("outline.head") : t("outline.off.title"),
      t("outline.rebuild.body"),
      [{ label: secs.length ? t("outline.rebuild.again") : t("outline.rebuild"),
         onClick: () => rebuildOutline() }]));
  } else if (!secs.length) {
    body.append(docNotice(t("outline.waiting.title"), t("outline.waiting.body"), []));
  }
  if (doc.error) {
    body.append(docNotice(t("outline.error", { reason: doc.error }),
                          t("outline.error.kept"), []));
  }
}

/* The document is drawn by JS, so a language switch has to redraw it -- the
 * data-i18n pass only reaches the markup. */
MW_I18N.onChange(() => {
  syncDocViewButton();
  if (state.docView) renderDocView();
});
