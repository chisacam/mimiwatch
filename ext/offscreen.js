/* 탭에서 나는 소리를 잡아 서버로 올립니다.
 *
 * **왜 별도 문서인가.** MV3 서비스 워커에는 getUserMedia 도 AudioContext 도
 * 없습니다. offscreen 문서는 화면에 뜨지 않는 진짜 페이지라 그 둘이 있습니다.
 *
 * mimiwatch 페이지의 탭 소리 받기와 같은 일을 하지만 잡는 방법이 다릅니다.
 * 저쪽은 사용자가 공유 창에서 탭을 고릅니다(getDisplayMedia). 여기서는
 * 확장이 그 탭을 이미 알고 있으므로 고르는 단계가 없고, 크롬이 탭 제목을
 * 감추는 문제도 없습니다.
 */
const INGEST_S = 2;
let cap = null;

async function start(streamId, sessionId, base) {
  stop();
  // 탭 캡처는 getDisplayMedia 가 아니라 이 모양으로 잡습니다. streamId 는
  // 서비스 워커가 chrome.tabCapture 로 받아 넘겨준 것입니다.
  const media = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });

  const ctx = new AudioContext({ sampleRate: 16000 });
  if (ctx.state === "suspended") await ctx.resume().catch(() => {});
  await ctx.audioWorklet.addModule("capture-worklet.js");
  const node = new AudioWorkletNode(ctx, "mimiwatch-capture");
  const source = ctx.createMediaStreamSource(media);
  source.connect(node);

  // **탭 소리를 되돌려 줍니다.** chrome.tabCapture 로 잡으면 그 탭의 소리가
  // 사용자에게 들리지 않게 됩니다. 우리가 다시 내보내지 않으면 방송이
  // 음소거된 것처럼 보입니다 -- mimiwatch 페이지 쪽(getDisplayMedia)에는
  // 없던 문제입니다.
  source.connect(ctx.destination);
  // 워클릿은 목적지까지 이어져 있어야 크롬이 돌립니다. 소리를 두 번
  // 내보내지 않도록 볼륨 0으로 잇습니다.
  const mute = ctx.createGain();
  mute.gain.value = 0;
  node.connect(mute).connect(ctx.destination);

  cap = { media, ctx, node, buf: [], n: 0, sending: false, sent: 0,
          id: sessionId, base, timer: null, warned: 0 };
  node.port.onmessage = (e) => { cap.buf.push(e.data); cap.n += e.data.length; };
  media.getAudioTracks()[0].addEventListener("ended", () => {
    chrome.runtime.sendMessage({ type: "captureEnded", sessionId });
    stop();
  });
  cap.timer = setInterval(() => flush(cap), INGEST_S * 1000);
}

async function flush(c) {
  if (!cap || cap !== c || c.sending || !c.n) return;
  const frames = c.buf, total = c.n;
  c.buf = []; c.n = 0;
  const pcm = new Int16Array(total);
  let i = 0;
  for (const f of frames) {
    for (let k = 0; k < f.length; k++) {
      const v = f[k];
      // 클리핑을 먼저 합니다. 넘친 값을 그대로 곱하면 int16 에서 감싸돌아
      // 큰 소리가 잡음으로 바뀝니다.
      pcm[i++] = v < -1 ? -32768 : v > 1 ? 32767 : Math.round(v * 32767);
    }
  }
  c.sending = true;
  try {
    const res = await fetch(`${c.base}/api/ingest/${encodeURIComponent(c.id)}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" },
      body: pcm.buffer,
    });
    const st = await res.json();
    c.sent += total;
    if (st.error) {
      chrome.runtime.sendMessage({ type: "captureError", error: st.error });
      stop();
      return;
    }
    if (st.dropped_s >= 1 && st.dropped_s !== c.warned) {
      c.warned = st.dropped_s;
      chrome.runtime.sendMessage({ type: "captureSlow", dropped: st.dropped_s });
    }
  } catch (_) {
    // 서버가 잠깐 못 받았습니다. 이 덩어리는 잃지만 다음 것은 갑니다.
  } finally {
    c.sending = false;
  }
}

function stop() {
  if (!cap) return;
  const c = cap;
  cap = null;
  if (c.timer) clearInterval(c.timer);
  try { c.node.disconnect(); } catch (_) {}
  try { c.ctx.close(); } catch (_) {}
  c.media.getTracks().forEach((t) => t.stop());
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
