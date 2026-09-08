/* mimiwatch front end — update: the notice strip up top and the
 * "Engines › Update" section.
 *
 * One of the files web/app.js was split into by concern. They are all plain
 * <script>s, read in the order index.html writes them down, and they share one
 * global scope -- module syntax is avoided here for the same reason as in
 * overlay.js, which is shared with the extension.
 *
 * The server (update.py) checks the GitHub releases once a day and announces a
 * newer version over the bus. This file draws that state and wires up the
 * download and apply buttons. "Later" goes quiet for that one tag only -- the
 * strip comes back when the next version appears. */

let updateInfo = null;

async function loadUpdateStatus() {
  try { renderUpdate(await (await fetch("/api/update")).json()); }
  catch (_) { /* An older version whose server does not know this endpoint. No strip, then. */ }
}

/* Called by bus.js. What the server announces has the same shape as status(). */
function onUpdateEvent(m) { renderUpdate(m); }

const updateDismissKey = (st) =>
  "mimiwatch.updateDismissed." + ((st.latest && st.latest.tag) || "");

function renderUpdate(st) {
  updateInfo = st;
  const box = $("update-notice");
  if (!box) return;
  const tag = (st.latest && st.latest.tag) || "";

  // The section in Engines always states the current state. Unlike the strip, it is never dismissed.
  const hint = $("update-hint");
  if (hint) {
    const bits = [t("update.hint.current", { version: st.current || "?" })];
    if (tag) bits.push(t("update.hint.latest", { tag }));
    if (st.error) bits.push(st.error);
    else if (tag && !st.available) bits.push(t("update.hint.uptodate"));
    else if (st.available) {
      bits.push(st.frozen ? t("update.hint.available") : t("update.hint.available.repo"));
    }
    hint.textContent = bits.join(" · ");
  }

  const text = $("update-text");
  const dl = $("update-download"), ap = $("update-apply"), later = $("update-dismiss");
  dl.hidden = ap.hidden = later.hidden = true;
  let dismissed = false;
  try { dismissed = !!localStorage.getItem(updateDismissKey(st)); } catch (_) { /* private window */ }
  const busy = ["downloading", "ready", "applying"].includes(st.state);
  if ((!st.available && !busy) || (dismissed && !busy)) { box.hidden = true; return; }

  if (st.state === "downloading") {
    const p = st.progress || {};
    const pct = p.total ? Math.round(p.done / p.total * 100) : null;
    text.textContent = pct === null ? t("update.downloading", { tag })
                                    : t("update.downloading.pct", { tag, pct });
  } else if (st.state === "ready") {
    text.textContent = t("update.ready", { tag });
    ap.hidden = false;
  } else if (st.state === "applying") {
    text.textContent = t("update.applying");
  } else if (st.state === "error" && st.error) {
    text.textContent = t("update.failed", { error: st.error });
    dl.hidden = !(st.frozen && st.latest && st.latest.asset);   // pressing it again resumes the download
    later.hidden = false;
  } else if (st.frozen && st.latest && st.latest.asset) {
    text.textContent = t("update.available", { tag });
    dl.hidden = false;
    later.hidden = false;
  } else {
    // Either running from the repository (not a bundle) or a release with no asset for this platform.
    text.textContent = t("update.available.repo", { tag });
    later.hidden = false;
  }
  box.hidden = false;
}

async function updatePost(path, extra) {
  const st = await (await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(extra || {}),
  })).json();
  if (st.error) alert(st.error);
  else renderUpdate(st);
  return st;
}

/* The repository token is kept in this browser only. It goes to api.github.com
 * and nowhere else, and the MIMIWATCH_GITHUB_TOKEN environment variable covers
 * the automatic check, which has no field to read. */
const TOKEN_KEY = "mimiwatch.updateToken";

function tokenValue() {
  const el = $("update-token");
  return el ? el.value.trim() : "";
}

async function applyUpdate() {
  if (!confirm(t("update.apply.confirm"))) return;
  const res = await (await fetch("/api/update/apply", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  })).json();
  if (res.error) { alert(res.error); return; }
  renderUpdate({ ...(updateInfo || {}), state: "applying", available: true });
  // The server goes down and comes back as the new version. Once it answers again, reload this page.
  const until = Date.now() + 180000;
  const timer = setInterval(async () => {
    if (Date.now() > until) { clearInterval(timer); return; }
    try {
      await fetch("/api/update", { cache: "no-store" });
      clearInterval(timer);
      location.reload();
    } catch (_) { /* still down */ }
  }, 2000);
}

/* This file is read before main.js, but it sits at the end of the document so
 * the elements are already there. We wire them up here without touching bind()
 * -- every element of the update belongs to this file. */
(function bindUpdate() {
  const on = (id, fn) => { const el = $(id); if (el) el.addEventListener("click", fn); };
  on("update-download", () => updatePost("/api/update/download", { token: tokenValue() }));
  on("update-apply", applyUpdate);
  on("update-dismiss", () => {
    try { if (updateInfo) localStorage.setItem(updateDismissKey(updateInfo), "1"); } catch (_) { /* harmless */ }
    $("update-notice").hidden = true;
  });
  on("update-check", async () => {
    const hint = $("update-hint");
    if (hint) hint.textContent = t("update.checking");
    await updatePost("/api/update/check", { token: tokenValue() });
  });
  const tokenEl = $("update-token");
  if (tokenEl) {
    try { tokenEl.value = localStorage.getItem(TOKEN_KEY) || ""; } catch (_) { /* private window */ }
    tokenEl.addEventListener("input", () => {
      try { localStorage.setItem(TOKEN_KEY, tokenEl.value); } catch (_) { /* private window */ }
    });
  }
  // The strip and the Update section are drawn from one answer and then sit
  // there, sometimes for days, so a language change redraws them from the
  // answer that is already in hand.
  MW_I18N.onChange(() => { if (updateInfo) renderUpdate(updateInfo); });
  loadUpdateStatus();
})();
