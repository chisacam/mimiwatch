/* The side that talks to the server. It goes out to localhost on the content
 * script's behalf.
 *
 * **Why it happens here.** A fetch from the content script goes out with that
 * page's (youtube.com) origin. Our server would then have to allow youtube.com
 * through `Access-Control-Allow-Origin`, and the moment it does, every script
 * running on the YouTube page can reach our server's write API. A fetch from
 * the service worker goes out with the extension's origin and `host_permissions`
 * skips CORS, so the server has to open nothing.
 *
 * **It does not use EventSource.** An MV3 service worker has none. Instead it
 * reads the fetch body as a stream and parses SSE by hand -- the format is
 * simple (`data: ...\n\n`) and we get to set the reconnect rule ourselves,
 * which is better anyway.
 *
 * **Why the worker does not die.** An MV3 service worker goes down after about
 * 30 seconds if left alone. A connected port with messages passing over it
 * restarts that clock. Subtitles arrive a line every few seconds, and during the
 * quiet it is poked once every KEEPALIVE_MS below.
 */

/* Strings come out of the table. A service worker has neither window nor DOM,
 * but i18n puts its global on globalThis and guards every place it touches the
 * DOM, so `t()` works here as it does elsewhere. */
importScripts("i18n.js", "strings-ext.js");

const BASE_KEY = "serverBase";
const LANG_KEY = "uiLang";
const DEFAULT_BASE = "http://localhost:8900";
const KEEPALIVE_MS = 20000;
// How long before reattaching after a break. Failing a few times while the
// server restarts is normal, so it waits quietly.
const RETRY_MS = 3000;

async function base() {
  const got = await chrome.storage.local.get(BASE_KEY);
  return got[BASE_KEY] || DEFAULT_BASE;
}

/* Error strings built here show on the popup's error line verbatim. So they
 * have to be in the same language as the popup, and the worker has no screen to
 * ask -- it reads the value the popup wrote down after getting the language from
 * the server. If nobody has asked yet, it goes with the browser's guess. The
 * worker goes down after about 30 s idle and this file runs again on every wake,
 * so it waits once before answering. */
async function loadLang() {
  const got = await chrome.storage.local.get(LANG_KEY);
  MW_I18N.setLang(got[LANG_KEY] || "");
}
const langReady = loadLang();

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes[LANG_KEY]) return;
  MW_I18N.setLang(changes[LANG_KEY].newValue || "");
});

async function api(path, init) {
  const res = await fetch((await base()) + path, init);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  const data = await res.json();
  // The popup is what normally writes the stored language down, but someone who
  // switches it in the web UI and then watches YouTube without ever opening the
  // popup would keep the old language on the overlay. Every path that needs the
  // config comes through here, so this is the one place that catches that.
  if (path === "/api/backends" && data && data.ui_lang) {
    const got = await chrome.storage.local.get(LANG_KEY);
    if (got[LANG_KEY] !== data.ui_lang) {
      await chrome.storage.local.set({ [LANG_KEY]: data.ui_lang });
    }
  }
  return data;
}

const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/* ---------- what the popup and the content script ask for ---------- */

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  // Anything headed for the offscreen document is not ours.
  // chrome.runtime.sendMessage goes to **every** receiver in the extension.
  // Answering "unknown request" from here would race the offscreen document's
  // real answer, and whichever arrives first wins.
  if (msg && msg.target === "offscreen") return;
  (async () => {
    try {
      await langReady;                      // know which language before answering
      if (msg.type === "sessions") reply({ ok: true, data: await api("/api/live/sessions") });
      else if (msg.type === "videos") reply({ ok: true, data: await api("/api/videos") });
      else if (msg.type === "backends") reply({ ok: true, data: await api("/api/backends") });
      else if (msg.type === "base") reply({ ok: true, data: await base() });
      else if (msg.type === "setBase") {
        await chrome.storage.local.set({ [BASE_KEY]: msg.base });
        // The YouTube tabs attached are holding a cue stream on the old address.
        // Make them reattach on the new one. A tab with no content script fails
        // quietly.
        const tabs = await chrome.tabs.query({ url: "https://www.youtube.com/*" });
        await Promise.all(tabs.map((t) =>
          chrome.tabs.sendMessage(t.id, { type: "reattach" }).catch(() => {})));
        reply({ ok: true });
      } else if (msg.type === "setViewer") {
        // The translation target is remembered on the server (backends.json),
        // shared with the web page. The worker is what reaches the server, so
        // the popup asks here. The local copy is already updated by the caller,
        // so a failed write only means the next open asks again.
        await post("/api/viewerlang", { lang: msg.lang }).catch(() => {});
        reply({ ok: true });
      } else if (msg.type === "watch") {
        reply({ ok: true, ...(await setWatch(msg.tabId, msg.value || "")) });
      } else if (msg.type === "dropWatch") {
        // The content script took itself down (it moved to a different video).
        // It does not know its own tab number, but we know it from sender. It
        // has already cleared the screen, so only the memory is erased --
        // sending a detach back from here would take down what just came down.
        const id = sender && sender.tab && sender.tab.id;
        if (id) await chrome.storage.local.set({ ["tab:" + id]: "" });
        reply({ ok: true });
      } else if (msg.type === "whatToWatch") {
        // The content script has just come up and is asking on its own. It does
        // not know its own tab number, but we know it from sender.
        const id = sender && sender.tab && sender.tab.id;
        const k = "tab:" + id;
        const v = id ? (await chrome.storage.local.get(k))[k] || "" : "";
        reply({ ok: true, data: v, videoId: await expectedVideo(v) });
      } else if (msg.type === "watching") {
        const k = "tab:" + msg.tabId;
        reply({ ok: true, data: (await chrome.storage.local.get(k))[k] || "" });
      } else if (msg.type === "startUrl") {
        reply(await startFromUrl(msg));
      } else if (msg.type === "startCapture") {
        reply(await startFromTab(msg));
      } else if (msg.type === "pushCookies") {
        reply(await pushCookies());
      } else if (msg.type === "resumeSession") {
        reply(await resumeSession(msg));
      } else if (msg.type === "stopSession") {
        await stopCapture();
        if (msg.sessionId) await post("/api/live/stop", { id: msg.sessionId });
        reply({ ok: true });
      } else reply({ ok: false, error: t("popup.errUnknownRequest", { type: msg.type }) });
    } catch (e) {
      reply({ ok: false, error: String((e && e.message) || e) });
    }
  })();
  return true;            // answers asynchronously
});

/* Decides what this tab watches and tells the content script about it.
 *
 * **It checks that it arrived.** The content script may not be there -- right
 * after reloading the extension (tabs already open keep using the old one), a
 * tab that is not YouTube, a tab that just opened and has not come up yet. It
 * used to fail quietly and that was that, which to the user looked like "I
 * picked it and nothing happens", and the page had to be reloaded before it
 * showed.
 *
 * If it did not arrive we reload the tab ourselves. The choice is already
 * stored, so the freshly started content script reads it on its own
 * (whatToWatch). **If it did arrive, we do not** -- the viewing position
 * jumping is a loss in itself. */
async function setWatch(tabId, value, opts) {
  await chrome.storage.local.set({ ["tab:" + tabId]: value });
  try {
    const videoId = await expectedVideo(value);
    const r = await chrome.tabs.sendMessage(
      tabId, value ? { type: "attach", value, videoId } : { type: "detach" });
    if (r && r.ok) return { delivered: true };
  } catch (_) { /* handled below */ }
  if (!value) return { delivered: false };   // taking it down is no reason to reload
  // No reload while the tab's sound is being captured. The captured stream is
  // tied to that tab, so a reload cuts off the transcription that just started.
  // A subtitle that is coming but simply not attaching to the screen, and
  // receiving itself being cut off, are two different stories.
  if (opts && opts.noReload) return { delivered: false, skippedReload: true };
  try {
    await chrome.tabs.reload(tabId);
    return { delivered: false, reloaded: true };
  } catch (e) {
    return { delivered: false, error: String(e.message || e) };
  }
}

/* Pulls the video id out of the URL. Knowing which video a session belongs to
 * is what stops old subtitles from going on being laid down after a move to a
 * different video. The same copy the content script uses (ytid.js). */
importScripts("ytid.js");
const videoIdOf = MimiYtId.videoIdOf;

/* Which video these subtitles belong to. The empty string when unknown -- that
 * means it could not be told, and then it asks rather than taking them down. */
async function expectedVideo(value) {
  if (!value) return "";
  // For a VOD the chosen value is the video id itself.
  if (!value.startsWith("live:")) return value;
  const sid = value.slice(5);
  const k = "vid:" + sid;
  const got = (await chrome.storage.local.get(k))[k];
  if (got) return got;
  try {
    // For a session started from a URL the server learns the video id through
    // yt-dlp. A session started from the tab's sound has none (there is no URL
    // to receive from).
    const st = await api(`/api/live/status/${encodeURIComponent(sid)}`);
    return st.video_id || "";
  } catch (_) {
    return "";
  }
}

/* ---------- handing the YouTube login cookies over ----------
 *
 * Receiving a members-only stream "From this URL" needs the server's yt-dlp to have the login
 * cookies. The extension can read even HttpOnly cookies with the `cookies` permission, so it
 * builds the cookies as of the moment the user pressed into Netscape format and hands them to
 * the server. **Only on a press** -- it is not a switch left on. They are read fresh each time,
 * so even when YouTube swaps the tab's cookies out (rotation) the newest ones at that moment go.
 *
 * The risk is the user's: running yt-dlp as an everyday account may get YouTube to put a bot
 * check or a temporary block on it. The popup says so next to the button. */
async function pushCookies() {
  const all = [];
  for (const domain of ["youtube.com", "google.com"]) {
    all.push(...await chrome.cookies.getAll({ domain }));
  }
  if (!all.some((c) => c.name === "SAPISID" || c.name === "__Secure-3PAPISID")) {
    return { ok: false, error: t("popup.errNoLoginCookies") };
  }
  // Netscape format: domain  includeSubdomains  path  secure  expiry  name  value.
  // HttpOnly is marked with the `#HttpOnly_` prefix curl and yt-dlp use.
  const lines = all.map((c) => {
    const domain = (c.hostOnly ? "" : ".") + c.domain.replace(/^\./, "");
    return [(c.httpOnly ? "#HttpOnly_" : "") + domain, c.hostOnly ? "FALSE" : "TRUE", c.path,
            c.secure ? "TRUE" : "FALSE", Math.floor(c.expirationDate || 0), c.name, c.value].join("\t");
  });
  const res = await post("/api/cookies/youtube", { cookies: lines.join("\n") + "\n" });
  if (res.error) return { ok: false, error: res.error };
  return { ok: true, count: res.count };
}

/* ---------- starting a session ---------- */

/* Starts from a URL. The server receives it directly through yt-dlp, so
 * transcription goes on even with the browser closed. A members-only stream
 * cannot be received down this path. */
async function startFromUrl(msg) {
  const probe = await post("/api/probe", { url: msg.url });
  if (probe.error) return { ok: false, error: probe.error };
  if (!probe.is_live) {
    return { ok: false, error: t("popup.errNotLive") };
  }
  const cfg = await api("/api/backends");
  const res = await post("/api/live/start", {
    url: msg.url, lang: msg.lang || null, viewer_lang: msg.viewerLang || "ko",
    backend: cfg.active, asr: cfg.asr_active, refine: !!msg.refine,
    genre: msg.genre || "general", profile: msg.profile || "broadcast",
  });
  if (res.error) return { ok: false, error: res.error };
  const w = await setWatch(msg.tabId, "live:" + res.id);
  return { ok: true, id: res.id, ...w };
}

/* Starts from the sound this tab makes. This is the path for what the server
 * cannot receive, such as a members-only stream.
 *
 * Actually capturing the sound is the offscreen document's job -- a service
 * worker has neither getUserMedia nor AudioContext. */
async function startFromTab(msg) {
  // Bring the offscreen document up **first**. A stream id "expires if not used within a few
  // seconds", and building the document can take that long. Creating the session on the server
  // also comes before getting the id.
  await ensureOffscreen();
  const cfg = await api("/api/backends");
  const res = await post("/api/live/capture", {
    title: msg.title || "", lang: msg.lang || null,
    viewer_lang: msg.viewerLang || "ko",
    backend: cfg.active, asr: cfg.asr_active, refine: !!msg.refine,
    genre: msg.genre || "general", profile: msg.profile || "broadcast",
  });
  if (res.error) return { ok: false, error: res.error };
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: msg.tabId });
  const started = await chrome.runtime.sendMessage({
    target: "offscreen", type: "capture",
    streamId, sessionId: res.id, base: await base(),
  });
  if (!started || !started.ok) {
    await post("/api/live/stop", { id: res.id });
    return { ok: false, error: (started && started.error) || t("popup.errNoAudio") };
  }
  // A tab-sound session has no video id for the server to know (there is no URL
  // to receive from). We write it down ourselves from the starting tab's URL.
  const vid = videoIdOf(msg.url || "");
  if (vid) await chrome.storage.local.set({ ["vid:" + res.id]: vid });
  const w = await setWatch(msg.tabId, "live:" + res.id, { noReload: true });
  return { ok: true, id: res.id, ...w };
}

/* Appends onto the same session that was stopped. The server revives the session
 * (one received from a URL with nothing missing, if it falls inside the rewind
 * window), and for one received from the tab's sound it captures this tab's sound
 * again and uploads it to that session. Then it lays it on this tab. */
async function resumeSession(msg) {
  // Resumes with the server's default engine -- the "Manage" choice on the mimiwatch page is
  // that value. With `msg.source` set it resumes from that source (one received from a URL onto
  // the tab's sound, or the other way round). Resuming a tab-sound session from a URL hands it
  // the current tab's URL -- that session has none.
  const cfg = await api("/api/backends");
  const res = await post("/api/live/resume", {
    id: msg.sessionId, asr: cfg.asr_active, backend: cfg.active,
    source: msg.source || "", url: msg.source === "hls" ? (msg.url || "") : "",
  });
  if (res.error) return { ok: false, error: res.error };
  const tab = res.source === "tab";
  if (tab) {
    await stopCapture();                  // release another session's sound if it was held
    await ensureOffscreen();              // a stream id expires in seconds, so the document first
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: msg.tabId });
    const started = await chrome.runtime.sendMessage({
      target: "offscreen", type: "capture",
      streamId, sessionId: res.id, base: await base(),
    });
    if (!started || !started.ok) {
      // The session came back but no sound is going to it. It is not ended --
      // press again and the server says it is already receiving, so then it has
      // to be stopped and resumed again.
      return { ok: false, error: (started && started.error)
        || t("popup.errNoAudioResume") };
    }
  }
  const w = await setWatch(msg.tabId, "live:" + res.id, { noReload: tab });
  return { ok: true, id: res.id, source: res.source, ...w };
}

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument();
  if (has) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "Takes the sound from the tab and sends it to the local mimiwatch server.",
  });
}

async function stopCapture() {
  if (await chrome.offscreen.hasDocument()) {
    try { await chrome.runtime.sendMessage({ target: "offscreen", type: "stop" }); }
    catch (_) { /* already down */ }
  }
}

/* ---------- the pipe the subtitles flow down ---------- */

/* Parses SSE by hand. All the server sends is a `data: {...}` line and a blank
 * line, so there is no need to handle the whole spec. */
/* `cursor.lastId` records the last `id:` received. Sent as `Last-Event-ID` on
 * reattach, it has the server resend only what came after -- two hours of
 * backlog used to come back whole every time the server paused for a moment. */
async function pump(url, onEvent, signal, cursor) {
  const headers = {};
  if (cursor && cursor.lastId) headers["Last-Event-ID"] = String(cursor.lastId);
  const res = await fetch(url, { signal, headers });
  if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let cut;
    // One event ends with a blank line. The server may send it in pieces, so it
    // is held until a complete chunk has gathered.
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let id = null, data = null;
      for (const line of chunk.split("\n")) {
        if (line.startsWith("id:")) id = line.slice(3).trim();
        else if (line.startsWith("data:")) data = line.slice(5).trim();
        // `: keepalive` flows past
      }
      if (data === null) continue;
      let ev;
      try { ev = JSON.parse(data); }
      catch (_) { continue; /* a malformed frame is thrown away */ }
      // The server closes the stream deliberately every 4.5 minutes (the service
      // worker's "5 minutes per request" rule). It leaves a mark saying this is
      // not the end, and the caller reattaches in a moment.
      if (ev && ev.type === "rotate") { if (cursor) cursor.rotated = true; continue; }
      onEvent(ev);
      if (id && cursor) cursor.lastId = id;
    }
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "cues") return;
  let abort = null, timer = null, closed = false;

  const stop = () => {
    closed = true;
    if (timer) clearInterval(timer);
    if (abort) { try { abort.abort(); } catch (_) {} }
    abort = null;
  };
  port.onDisconnect.addListener(stop);

  port.onMessage.addListener(async (msg) => {
    if (msg.type !== "attach") return;
    stop();
    closed = false;
    const b = await base();
    const value = msg.value || "";
    const sid = value.startsWith("live:") ? value.slice(5) : "";

    timer = setInterval(() => {
      // Keeps the worker alive during the quiet. A message over the port
      // restarts the idle clock.
      if (!closed) { try { port.postMessage({ type: "tick" }); } catch (_) {} }
    }, KEEPALIVE_MS);

    if (!sid) {
      try {
        port.postMessage({ type: "doc",
          data: await api(`/api/video/${encodeURIComponent(value)}`) });
      } catch (e) {
        port.postMessage({ type: "error", error: String(e.message || e) });
      }
      return;
    }

    // Live can break -- a server restart, the stream ending, sleep. It
    // reattaches quietly. The server resends what was missed (or, past the log,
    // every subtitle piled up) right after connecting, so no line drops out of
    // a reattach.
    const cursor = { lastId: null, rotated: false };
    while (!closed) {
      abort = new AbortController();
      try {
        await pump(`${b}/api/live/events/${encodeURIComponent(sid)}`,
                   (e) => { if (!closed) port.postMessage({ type: "event", data: e }); },
                   abort.signal, cursor);
        if (closed) return;
        if (cursor.rotated) { cursor.rotated = false; continue; }   // back with Last-Event-ID shortly
        // A finished session has the server send the whole backlog and close.
        // That is a normal end.
        port.postMessage({ type: "ended" });
        return;
      } catch (e) {
        if (closed) return;
        port.postMessage({ type: "stalled", error: String(e.message || e) });
        await new Promise((r) => setTimeout(r, RETRY_MS));
      }
    }
  });
});

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.local.remove("tab:" + tabId);
});
