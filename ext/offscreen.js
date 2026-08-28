/* 탭에서 나는 소리를 잡아 서버로 올립니다.
 *
 * **왜 별도 문서인가.** MV3 서비스 워커에는 getUserMedia 도 AudioContext 도
 * 없습니다. offscreen 문서는 화면에 뜨지 않는 진짜 페이지라 그 둘이 있습니다.
 *
 * mimiwatch 페이지의 탭 소리 받기와 같은 일을 하지만 잡는 방법이 다릅니다.
 * 저쪽은 사용자가 공유 창에서 탭을 고릅니다(getDisplayMedia). 여기서는
 * 확장이 그 탭을 이미 알고 있으므로 고르는 단계가 없고, 크롬이 탭 제목을
 * 감추는 문제도 없습니다. 잡은 뒤의 일(16kHz 그래프·int16·2초마다 올리기)은
 * 페이지와 공유하는 capture.js 가 합니다.
 */
let cap = null;
let out = null;

async function start(streamId, sessionId, base) {
  stop();
  // 탭 캡처는 getDisplayMedia 가 아니라 이 모양으로 잡습니다. streamId 는
  // 서비스 워커가 chrome.tabCapture 로 받아 넘겨준 것입니다.
  const media = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });

  /* **듣는 길과 받아 적는 길을 나눕니다.**
   *
   * chrome.tabCapture 로 잡으면 그 탭의 소리가 사용자에게 들리지 않게
   * 됩니다. 우리가 다시 내보내야 하는데, 예전에는 그것을 16kHz
   * AudioContext 의 destination 으로 보냈습니다. 인식기가 원하는 모양이지
   * 사람이 들을 모양이 아닙니다 -- 48kHz 스테레오가 16kHz 로 깎여 나가
   * 전화 음질이 되었습니다. 자막을 얻는 대신 음질과 채널을 잃는 거래는
   * 수지가 맞지 않습니다.
   *
   * 듣는 쪽은 잡은 스트림을 **그대로** 내보냅니다. <audio> 에 붙이면 우리
   * 그래프를 거치지 않으므로 표본율도 채널도 원본 그대로입니다. */
  out = new Audio();
  out.srcObject = media;
  out.autoplay = true;
  await out.play().catch((e) => {
    chrome.runtime.sendMessage({ type: "captureError",
      error: "소리를 되돌려 주지 못했습니다: " + (e.message || e) });
  });

  // 받아 적는 쪽만 16kHz 모노입니다.
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
  MimiCapture.stop(c);
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
