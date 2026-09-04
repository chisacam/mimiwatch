/* One copy of folding the tab's sound down to 16kHz mono int16 PCM and posting
 * it to the server.
 *
 * The mimiwatch page (a tab picked through getDisplayMedia) and the extension's
 * offscreen document (chrome.tabCapture) do the same job and differ only in
 * **how they get the stream**. Everything after that -- the Web Audio graph,
 * the worklet, the int16 conversion, the upload every 2 seconds, the overflow
 * warning -- existed in two copies, and an audio-quality fix in one stayed
 * broken in the other. One copy lives here and is copied into the extension
 * folder (bench/ext_check.py compares them).
 *
 *     const cap = await MimiCapture.start({
 *       media,                       // MediaStream (needs an audio track)
 *       workletUrl,                  // the URL of capture-worklet.js
 *       post: (arrayBuffer) => fetch(...).then(r => r.json()),   // returns the server's answer
 *       onEnded, onError(msg), onDropped(seconds),
 *     });
 *     MimiCapture.stop(cap);
 *
 * Playing the sound back to the user is not done here. This graph is the shape
 * the recognizer wants (16kHz mono), not the shape a person listens to -- the
 * side that has to play it back (the extension's tabCapture) attaches the
 * captured stream to an <audio> as it is.
 */
(function (root) {
  "use strict";

  /* How many seconds go up at a time. Shorter and the subtitle comes out
   * sooner but the requests pile up; longer and it is the other way round. At
   * 2 s it is 0.5 requests a second and a 64KB chunk, a burden on neither side,
   * and the delay is already buried in the time VAD spends waiting for the
   * utterance to end. */
  const INGEST_S = 2;

  /* float32 fragments into one int16. Clipping comes first -- multiplying an
   * already overflowed value wraps around in int16 and turns a loud sound into
   * noise. */
  function toPCM(frames, total) {
    const pcm = new Int16Array(total);
    let i = 0;
    for (const f of frames) {
      for (let k = 0; k < f.length; k++) {
        const v = f[k];
        pcm[i++] = v < -1 ? -32768 : v > 1 ? 32767 : Math.round(v * 32767);
      }
    }
    return pcm;
  }

  async function start(opts) {
    const ctx = new AudioContext({ sampleRate: 16000 });
    // Chrome's autoplay policy can have a new AudioContext born suspended. In
    // that state the worklet never runs once, so the screen says "receiving"
    // and not one subtitle line is added -- worse, nothing errors anywhere.
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    await ctx.audioWorklet.addModule(opts.workletUrl);
    // Folding stereo down to one channel is left to Web Audio. Picking the
    // left channel by hand would lose a voice panned right entirely.
    const node = new AudioWorkletNode(ctx, "mimiwatch-capture", {
      channelCount: 1,
      channelCountMode: "explicit",
      channelInterpretation: "speakers",
    });
    ctx.createMediaStreamSource(opts.media).connect(node);
    // Chrome will not run the worklet at all unless it is connected through to
    // a destination. This graph is not a listening path, so it connects at
    // volume 0.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(ctx.destination);

    const cap = { media: opts.media, ctx, node, buf: [], n: 0, sent: 0,
                  sending: false, warned: 0, timer: null, stopped: false, opts };
    node.port.onmessage = (e) => { cap.buf.push(e.data); cap.n += e.data.length; };
    // This is where it lands when the user presses Chrome's "Stop sharing" or
    // closes the tab.
    const track = opts.media.getAudioTracks()[0];
    if (track) {
      track.addEventListener("ended", () => {
        if (cap.stopped) return;
        stop(cap);
        if (opts.onEnded) opts.onEnded();
      });
    }
    cap.timer = setInterval(() => flush(cap), INGEST_S * 1000);
    return cap;
  }

  async function flush(cap) {
    // If the previous upload has not finished, this one goes with the next
    // round. Overlapping uploads land in the server's queue out of order.
    if (cap.stopped || cap.sending || !cap.n) return;
    const frames = cap.buf, total = cap.n;
    cap.buf = []; cap.n = 0;
    cap.sent += total;
    const pcm = toPCM(frames, total);
    cap.sending = true;
    try {
      const st = await cap.opts.post(pcm.buffer);
      if (st && st.error) {
        // The session is gone. No reason to keep uploading sound nobody hears.
        stop(cap);
        if (cap.opts.onError) cap.opts.onError(st.error);
        return;
      }
      if (st && st.dropped_s >= 1 && st.dropped_s !== cap.warned) {
        cap.warned = st.dropped_s;
        if (cap.opts.onDropped) cap.opts.onDropped(st.dropped_s);
      }
    } catch (_) {
      // The server missed one for a moment. This chunk is lost, the next goes.
    } finally {
      cap.sending = false;
    }
  }

  function stop(cap) {
    if (!cap || cap.stopped) return;
    cap.stopped = true;
    if (cap.timer) clearInterval(cap.timer);
    try { cap.node.disconnect(); } catch (_) {}
    try { cap.ctx.close(); } catch (_) {}
    // The tracks have to be released for Chrome's "sharing" mark to go away.
    cap.media.getTracks().forEach((t) => t.stop());
  }

  root.MimiCapture = { start, stop, toPCM, INGEST_S };
})(typeof window !== "undefined" ? window : globalThis);
