/* 탭 소리를 16kHz 모노 int16 PCM으로 접어 서버로 올리는 한 벌.
 *
 * mimiwatch 페이지(getDisplayMedia로 고른 탭)와 확장의 offscreen 문서
 * (chrome.tabCapture)가 같은 일을 하는데, 스트림을 **어떻게 얻는지**만
 * 다릅니다. 얻은 뒤의 일 -- Web Audio 그래프, 워클릿, int16 변환, 2초마다
 * 올리기, 넘침 경고 -- 은 두 벌이었고 한쪽에서 고친 음질 문제가 다른 쪽에
 * 남았습니다. 여기 한 벌만 두고 확장 폴더에 복사합니다(bench/ext_check.py가
 * 사본을 맞댑니다).
 *
 *     const cap = await MimiCapture.start({
 *       media,                       // MediaStream (오디오 트랙 필요)
 *       workletUrl,                  // capture-worklet.js 의 주소
 *       post: (arrayBuffer) => fetch(...).then(r => r.json()),   // 서버 응답을 돌려줍니다
 *       onEnded, onError(msg), onDropped(seconds),
 *     });
 *     MimiCapture.stop(cap);
 *
 * 소리를 사용자에게 되돌려 주는 일은 여기 없습니다. 이 그래프는 인식기가
 * 원하는 모양(16kHz 모노)이지 사람이 들을 모양이 아닙니다 -- 되돌려 줘야
 * 하는 쪽(확장의 tabCapture)은 잡은 스트림을 <audio>에 그대로 붙입니다.
 */
(function (root) {
  "use strict";

  /* 한 번에 올릴 길이(초). 짧을수록 자막이 빨리 나오지만 요청이 늘고, 길수록
   * 그 반대입니다. 2초면 초당 요청 0.5회에 덩어리 64KB라 어느 쪽도 부담이
   * 아니고, 지연은 VAD가 발화를 끊기까지 기다리는 시간에 이미 묻힙니다. */
  const INGEST_S = 2;

  /* float32 조각들을 int16 하나로. 클리핑을 먼저 합니다 -- 넘친 값을 그대로
   * 곱하면 int16에서 감싸돌아 큰 소리가 잡음으로 바뀝니다. */
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
    // 크롬의 자동재생 정책 때문에 새 AudioContext는 suspended로 태어날 수
    // 있습니다. 그 상태에서는 워클릿이 한 번도 돌지 않아, 화면은 「받는 중」인데
    // 자막이 한 줄도 늘지 않습니다 -- 아무 데도 오류가 나지 않아 더 나쁩니다.
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    await ctx.audioWorklet.addModule(opts.workletUrl);
    // 스테레오를 한 채널로 접는 일은 Web Audio에 맡깁니다. 손으로 왼쪽만
    // 집으면 오른쪽에 치우친 목소리를 통째로 놓칩니다.
    const node = new AudioWorkletNode(ctx, "mimiwatch-capture", {
      channelCount: 1,
      channelCountMode: "explicit",
      channelInterpretation: "speakers",
    });
    ctx.createMediaStreamSource(opts.media).connect(node);
    // 워클릿이 목적지까지 이어져 있지 않으면 크롬이 아예 돌리지 않습니다.
    // 이 그래프는 듣는 길이 아니므로 볼륨 0으로 잇습니다.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(ctx.destination);

    const cap = { media: opts.media, ctx, node, buf: [], n: 0, sent: 0,
                  sending: false, warned: 0, timer: null, stopped: false, opts };
    node.port.onmessage = (e) => { cap.buf.push(e.data); cap.n += e.data.length; };
    // 사용자가 크롬의 「공유 중지」를 누르거나 탭을 닫으면 여기로 옵니다.
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
    // 앞선 전송이 아직 안 끝났으면 이번 것은 다음 차례에 같이 보냅니다.
    // 겹쳐 보내면 서버 쪽 큐에 순서가 뒤집힌 채로 들어갑니다.
    if (cap.stopped || cap.sending || !cap.n) return;
    const frames = cap.buf, total = cap.n;
    cap.buf = []; cap.n = 0;
    cap.sent += total;
    const pcm = toPCM(frames, total);
    cap.sending = true;
    try {
      const st = await cap.opts.post(pcm.buffer);
      if (st && st.error) {
        // 세션이 없어졌습니다. 아무도 듣지 않는 소리를 계속 올릴 이유가 없습니다.
        stop(cap);
        if (cap.opts.onError) cap.opts.onError(st.error);
        return;
      }
      if (st && st.dropped_s >= 1 && st.dropped_s !== cap.warned) {
        cap.warned = st.dropped_s;
        if (cap.opts.onDropped) cap.opts.onDropped(st.dropped_s);
      }
    } catch (_) {
      // 서버가 잠깐 못 받았습니다. 이 덩어리는 잃지만 다음 것은 갑니다.
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
    // 트랙을 놓아야 크롬의 "공유 중" 표시가 사라집니다.
    cap.media.getTracks().forEach((t) => t.stop());
  }

  root.MimiCapture = { start, stop, toPCM, INGEST_S };
})(typeof window !== "undefined" ? window : globalThis);
