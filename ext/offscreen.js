/* Captures the sound a tab makes and uploads it to the server.
 *
 * **Why a separate document.** An MV3 service worker has neither getUserMedia
 * nor AudioContext. An offscreen document is a real page that never shows on
 * screen, and it has both.
 *
 * It does the same job as the mimiwatch page's tab-sound receiving, but captures
 * it differently. Over there the user picks the tab in the sharing dialog
 * (getDisplayMedia). Here the extension already knows which tab it is, so there
 * is no picking step and no trouble with Chrome hiding the tab title. Everything
 * after the capture (the 16kHz graph, int16, the upload every 2 seconds) is done
 * by capture.js, shared with the page.
 */
let cap = null;
let out = null;

async function start(streamId, sessionId, base) {
  stop();
  // Tab capture is grabbed in this shape, not through getDisplayMedia. The
  // streamId is what the service worker got from chrome.tabCapture and passed
  // over.
  const media = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });

  /* **The listening path and the transcription path are split.**
   *
   * Capturing through chrome.tabCapture stops that tab's sound reaching the
   * user. We have to play it back out, and that used to go to the destination of
   * the 16kHz AudioContext. That is the shape the recognizer wants, not the
   * shape a person listens to -- 48kHz stereo was cut down to 16kHz and came out
   * at telephone quality. Losing audio quality and channels in exchange for
   * subtitles is not a trade that pays.
   *
   * The listening side plays the captured stream back **as it is**. Attached to
   * an <audio> it never passes through our graph, so both the sample rate and
   * the channels stay as they were. */
  out = new Audio();
  out.srcObject = media;
  out.autoplay = true;
  await out.play().catch((e) => {
    chrome.runtime.sendMessage({ type: "captureError",
      error: "Could not play the sound back: " + (e.message || e) });
  });

  // Only the transcription side is 16kHz mono.
  cap = await MimiCapture.start({
    media,
    workletUrl: "capture-worklet.js",
    post: (buf) => fetch(`${base}/api/ingest/${encodeURIComponent(sessionId)}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" },
      body: buf,
    }).then((r) => r.json()),
    onEnded: () => { chrome.runtime.sendMessage({ type: "captureEnded", sessionId }); stop(); },
    onError: (msg) => { chrome.runtime.sendMessage({ type: "captureError", error: msg }); stop(); },
    onDropped: (s) => chrome.runtime.sendMessage({ type: "captureSlow", dropped: s }),
  });
}

function stop() {
  if (out) { try { out.pause(); out.srcObject = null; } catch (_) {} out = null; }
  if (!cap) return;
  const c = cap;
  cap = null;
  // stopAndFlush, not stop: the last up-to-2 s the upload timer had not caught
  // would otherwise go nowhere. Not awaited -- the caller is a message handler
  // that has to reply now.
  MimiCapture.stopAndFlush(c);
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.target !== "offscreen") return;
  if (msg.type === "capture") {
    start(msg.streamId, msg.sessionId, msg.base)
      .then(() => reply({ ok: true }))
      .catch((e) => reply({ ok: false, error: String(e.message || e) }));
    return true;
  }
  if (msg.type === "stop") { stop(); reply({ ok: true }); return true; }
  if (msg.type === "state") {
    reply({ ok: true, running: !!cap, sent: cap ? cap.sent : 0 });
    return true;
  }
});
