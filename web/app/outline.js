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

/* Live means **still receiving**, not "a session is on screen". It used to be
 * the second, and a session that had stopped kept offering "Start writing":
 * the server has retired that session by then and answers "no such session",
 * so the button did nothing, and the rebuild -- the path that exists for a
 * finished session -- was never offered at all. */
function outlineIsLive() {
  return !!(state.live && state.live.id) && isLiveReceiving();
}

/* The id the server files a job under (store.owner_of): a picker value can
 * carry a `live:` prefix the job's owner does not. */
function outlineKey(v) {
  return (v || "").startsWith("live:") ? v.slice(5) : (v || "");
}

/* What the page knows about writing the document besides the document itself.
 * Each remembers the owner it is about and is only drawn while that owner is
 * on screen, so a tile switch needs no reset hook of its own.
 *   outlineJob    -- a rebuild running: {owner, id, done, total}
 *   outlineBusy   -- the owner a request was just sent for and has not answered.
 *                    The button is disabled from the click, not from the
 *                    reply: the gap between the two is a double click too.
 *   outlineNotice -- why the last request was refused: {owner, error, reason} */
let outlineJob = null;
let outlineBusy = "";
let outlineNotice = null;
let outlineLastLive = null;

function outlineMine(x) {
  return !!x && outlineKey(x.owner) === outlineKey(outlineOwner());
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
    // A rebuild already under way when this page opened. Its job frames went
    // out before this page was listening, so this is the only way it learns.
    if (doc.job && doc.job.id) {
      outlineJob = { owner, id: doc.job.id, done: doc.job.done || 0,
                     total: doc.job.total || 0 };
    } else if (outlineMine(outlineJob) && outlineJob.id) {
      outlineJob = null;
    }
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
    if (outlineOwner() === owner) {
      // The refusal used to be dropped here, so a press that the server
      // turned down looked like a press that did nothing.
      if (doc.error) outlineNotice = { owner, error: doc.error };
      else { state.outline = doc; outlineNotice = null; }
    }
  } catch (err) {
    console.error("[outline]", err);
    if (outlineOwner() === owner) outlineNotice = { owner, error: "", reason: String(err) };
  }
  renderDocView();
}

async function rebuildOutline() {
  const owner = outlineOwner();
  if (!owner || outlineBusy === owner || outlineMine(outlineJob)) return;
  outlineBusy = owner;
  outlineNotice = null;
  renderDocView();
  let res = null;
  try {
    const r = await fetch("/api/outline/rebuild", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: owner }),
    });
    res = await r.json();
  } catch (err) {
    console.error("[outline]", err);
    res = { error: "", reason: String(err) };
  }
  if (outlineBusy === owner) outlineBusy = "";
  if (outlineOwner() !== owner) return;
  // The reply used to be thrown away. The server names *why* it refused with
  // an id (jobs.start_outline) precisely so the screen can say something
  // different for each, and none of them was ever shown.
  if (res && res.job) {
    // A job frame may already have landed and moved the count on; the reply
    // does not set it back.
    if (!(outlineMine(outlineJob) && outlineJob.id === res.job)) {
      outlineJob = { owner, id: res.job, done: 0, total: res.total || 0 };
    }
  } else {
    outlineNotice = { owner, error: (res && res.error) || "", reason: res && res.reason };
  }
  // What happens next arrives on the change feed (bus.js) as job frames.
  renderDocView();
}

async function cancelOutlineRebuild() {
  if (!outlineMine(outlineJob)) return;
  try {
    await fetch("/api/job/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: outlineJob.id }),
    });
  } catch (err) {
    console.error("[outline]", err);
  }
}

/* A refusal id, as a notice. The ids are the ones jobs.start_outline and
 * live.outline_set answer with; anything else is shown as it came. */
function outlineRefusal(n) {
  switch (n.error) {
    case "backend-cannot-write":
      return docNotice(t("outline.blocked.title"), t("outline.blocked.body"),
                       [{ label: t("outline.blocked.action"),
                          onClick: () => openSettings() }]);
    case "no-subtitles":
      return docNotice(t("outline.fail.title"), t("outline.fail.noSubtitles"), []);
    case "session-still-live":
      return docNotice(t("outline.fail.title"), t("outline.fail.stillLive"), []);
    case "no such session":
      return docNotice(t("outline.fail.title"), t("outline.fail.gone"), []);
    default:
      return docNotice(t("outline.fail.title"),
                       t("outline.fail.other", { reason: n.reason || n.error || "?" }), []);
  }
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
  if (!m.owner || outlineKey(m.owner) !== outlineKey(outlineOwner())) return;
  if (state.docView) loadOutline(outlineOwner());
}

/* The rebuild job's own state, off the change feed (bus.js routes every job of
 * kind `outline` here). The `outline` frame above only goes out once a section
 * exists, so a job that fails before its first one never sends it -- keyed on
 * that frame alone, "Writing…" would stand for ever. The job frame says how far
 * it got and how it ended. */
function onOutlineJobState(st) {
  if (!st.owner || outlineKey(st.owner) !== outlineKey(outlineOwner())) return;
  const owner = outlineOwner();
  if (st.state === "running") {
    outlineJob = { owner, id: st.id, done: st.done || 0, total: st.total || 0 };
  } else {
    if (outlineJob && outlineJob.id === st.id) outlineJob = null;
    if (st.state === "error" || st.state === "interrupted") {
      outlineNotice = { owner, error: st.error || "", reason: st.error || st.state };
    }
    if (state.docView) loadOutline(owner);
  }
  if (state.docView) renderDocView();
}

/* A session's state changed (live.js renderLiveStatus). The view only has to
 * be redrawn when that flips it between live and finished -- a session that
 * stops with the view open would otherwise keep showing "Start writing" -- and
 * status frames are too frequent to rebuild the view on every one. */
function syncDocViewLive() {
  const now = outlineIsLive();
  if (now === outlineLastLive) return;
  outlineLastLive = now;
  if (state.docView) {
    renderDocView();
    if (!now) loadOutline(outlineOwner());
  }
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
    btn.disabled = !!a.disabled;
    if (!a.disabled) btn.onclick = a.onClick;
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
    const job = outlineMine(outlineJob) ? outlineJob : null;
    const busy = !!job || outlineBusy === owner;
    const label = job
      ? t("outline.rebuild.running", { done: job.done || 0, total: job.total || 0 })
      : secs.length ? t("outline.rebuild.again") : t("outline.rebuild");
    const actions = [{ label, disabled: busy, onClick: () => rebuildOutline() }];
    if (job) actions.push({ label: t("outline.rebuild.cancel"),
                            onClick: () => cancelOutlineRebuild() });
    body.append(docNotice(
      secs.length ? t("outline.head") : t("outline.off.title"),
      t("outline.rebuild.body"), actions));
  } else if (!secs.length) {
    body.append(docNotice(t("outline.waiting.title"), t("outline.waiting.body"), []));
  }
  if (doc.error) {
    body.append(docNotice(t("outline.error", { reason: doc.error }),
                          t("outline.error.kept"), []));
  }
  // Not when it says what the blocked notice or the stored error above
  // already says -- one reason on screen twice reads as two failures.
  const n = outlineMine(outlineNotice) ? outlineNotice : null;
  if (n && !(n.error === "backend-cannot-write" && doc.can_outline === false)
        && !(doc.error && ((n.reason || "").startsWith(doc.error) || n.error === doc.error))) {
    body.append(outlineRefusal(n));
  }
}

/* The document is drawn by JS, so a language switch has to redraw it -- the
 * data-i18n pass only reaches the markup. */
MW_I18N.onChange(() => {
  syncDocViewButton();
  if (state.docView) renderDocView();
});
