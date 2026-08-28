/* mimiwatch 화면 — 탭 소리 받기(getDisplayMedia → 16kHz PCM → /api/ingest)와 탭 세션의 이름.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

/* ---------- 탭 오디오 받기 -------------------------------------------
 *
 * 멤버십 전용 방송은 서버가 받을 수 없습니다. yt-dlp에 쿠키를 물려도 유튜브가
 * 열린 탭의 쿠키를 계속 갈아 치우고, 애초에 그 길은 약관을 비껴갑니다.
 *
 * 대신 사용자가 이미 듣고 있는 소리를 받습니다. 공유 대화상자에서 본인이
 * 직접 탭을 고르므로 우회가 아니고, 쿠키도 필요 없습니다. 크롬 계열 전용
 * 입니다 -- 탭 오디오 공유를 주는 브라우저가 그쪽뿐입니다.
 *
 * 서버가 받는 것은 16kHz 모노 int16 PCM입니다. AudioContext를 16000으로 열면
 * 크롬이 리샘플까지 해 주므로 여기서 표본율을 만질 일이 없습니다. */
/* 한 번에 올릴 길이. 짧을수록 자막이 빨리 나오지만 요청이 늘고, 길수록
 * 그 반대입니다. 2초면 초당 요청 0.5회에 덩어리 64KB라 어느 쪽도 부담이
 * 아니고, 지연은 VAD가 발화를 끊기까지 기다리는 시간에 이미 묻힙니다. */
const INGEST_S = 2;

function stopCapture() {
  const c = state.capture;
  if (!c) return;
  state.capture = null;
  if (c.timer) clearInterval(c.timer);
  try { c.node.disconnect(); } catch (_) {}
  try { c.ctx.close(); } catch (_) {}
  // 트랙을 놓아야 크롬의 "공유 중" 표시가 사라집니다.
  c.stream.getTracks().forEach(t => t.stop());
}

/* 탭을 고르게 합니다. **세션을 만들기 전에** 부릅니다.
 *
 * 두 가지 이유가 있습니다. getDisplayMedia는 사용자 조작 직후에만 열리는데
 * 그 유효기간이 몇 초뿐이라, 서버 왕복을 먼저 하면 창이 안 뜰 수 있습니다.
 * 그리고 여기서 취소하면 아직 아무것도 만들지 않았으므로 치울 것도 없습니다.
 *
 * 돌려주는 것은 MediaStream이거나, 못 얻었으면 null입니다. */
async function requestTabAudio() {
  let media;
  try {
    // video:true가 필요합니다. 크롬은 오디오만 요청하면 탭 선택지를 아예
    // 내놓지 않습니다. 받은 화면은 쓰지 않고 버립니다.
    media = await navigator.mediaDevices.getDisplayMedia({
      video: true, audio: true,
    });
  } catch (err) {
    if (err && err.name === "NotAllowedError") return null;   // 사용자가 취소
    jobError("탭 소리를 받지 못했습니다: " + (err && err.message || err));
    return null;
  }
  if (!media.getAudioTracks().length) {
    media.getTracks().forEach(t => t.stop());
    jobError("소리가 함께 오지 않았습니다. 공유 창에서 탭을 고르고 "
             + "「탭 오디오도 공유」를 켜 주십시오.");
    return null;
  }
  return media;
}

/* 탭 세션의 안내는 **화면 자리**에만 씁니다.
 *
 * 위쪽 막대에도 같은 말을 띄웠더니 한 화면에 두 번 나왔습니다. 그 자리는
 * 좁고(대본 창에서는 460px입니다) 눌러야 할 것을 알리는 데 써야 하므로,
 * 설명은 비어 있는 플레이어 자리로 내립니다. 어차피 그 자리는 이 흐름에서
 * 검은 사각형으로 남습니다.
 *
 * 시작할 때와 이어받을 때 모두 부릅니다 -- 이어받기는 attachLive 를 지나며
 * clearPlayerError() 로 이 안내를 지우고 갑니다. */
function tabStageNotice(tail) {
  playerError("이 방송은 여기서 재생하지 않습니다. 소리만 다른 탭에서 "
              + "받아 적고 있습니다 — 유튜브 탭에서 보시고, 자막 내역은 옆 "
              + "창이나 오른쪽 스크립트에서 읽으십시오."
              + (tail ? " " + tail : ""));
}

/* 이름 고치기.
 *
 * 탭 소리에만 답니다. 주소로 받는 세션은 yt-dlp 가 제목을 가져오고, 이어받을
 * 때 다시 가져오므로 여기서 고쳐 봐야 되돌아갑니다. */
function syncRenameButton() {
  const live = state.live;
  $("rename-live").hidden = !(live && live.source === "tab");
}

function renameLive() {
  const live = state.live;
  if (!live) return;
  const box = $("now-title");
  const cur = box.textContent.trim();
  const input = document.createElement("input");
  input.className = "title-edit";
  input.value = cur === "탭 오디오" ? "" : cur;
  input.placeholder = "무엇을 듣고 있는지";
  box.replaceWith(input);
  input.focus();
  input.select();

  let closed = false;
  const done = async (save) => {
    if (closed) return;          // blur 와 Enter 가 겹쳐 두 번 들어옵니다
    closed = true;
    const text = input.value.trim();
    input.replaceWith(box);
    if (!save || !text || text === cur) return;
    const r = await (await fetch("/api/live/title", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: live.id, title: text }),
    })).json();
    if (r.error) { jobError(r.error); return; }
    setNowTitle(text);
    if (state.doc) state.doc.title = text;
    if (live.probe) live.probe.title = text;
    // 목록의 그 줄도 같이 고칩니다. 목록을 통째로 다시 그리면 받는 중인
    // 세션의 임시 줄이 사라졌다 돌아오며 깜빡입니다.
    const row = $("video-list").querySelector(
      `.video-row[data-value="${CSS.escape("live:" + live.id)}"]`);
    if (row) {
      row.dataset.title = text;
      const t = row.querySelector(".vt");
      if (t) t.textContent = text;
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); done(true); }
    if (e.key === "Escape") { e.preventDefault(); done(false); }
  });
  input.addEventListener("blur", () => done(true));
}

/* 공유받은 대상의 이름 -- 쓸 수 있으면.
 *
 * **탭에서는 못 씁니다.** 크롬은 탭을 캡처할 때 label 에 탭 제목이 아니라
 * 불투명한 식별자를 넣습니다. 실제로 받은 값입니다:
 *
 *     web-contents-media-stream://8D6FD737C5BFC47DBCE78F63FA28FECB
 *
 * 그것을 제목으로 쓰면 「탭 오디오」보다 나쁘므로 걸러 내고 빈 값을
 * 돌려줍니다. 이름은 「＋ 추가」에서 적거나 나중에 「✎ 이름」으로 고칩니다.
 *
 * 그래도 이 함수를 남겨 둡니다. 창이나 화면을 고르면 그쪽 이름이 오고,
 * 다른 크로미움 판이 진짜 제목을 줄 수도 있습니다. 오면 씁니다. */
function tabTitleFrom(media) {
  const v = media.getVideoTracks()[0];
  const raw = ((v && v.label) || "").trim();
  if (!raw) return "";
  // screen:0:0, window:12:0, web-contents-media-stream://5/12 …
  if (/^[a-z][a-z-]*:(\/\/)?[\d:/]/i.test(raw)) return "";
  return raw.replace(/\s+[-–—]\s+(Google Chrome|Chromium|Chrome)$/i, "")
            .slice(0, 200);
}

/* 탭이 아니라 창이나 화면 전체를 골랐는가. 소리가 따라오는지는 플랫폼마다
 * 다른데, 탭은 어디서나 따라옵니다. */
function isTabSurface(media) {
  const v = media.getVideoTracks()[0];
  const s = v && v.getSettings ? v.getSettings() : null;
  // 설정을 못 읽으면 탭이라고 봅니다 -- 아니라면 소리가 없어 앞에서 걸립니다.
  return !s || !s.displaySurface || s.displaySurface === "browser";
}

/* 받아 둔 스트림을 세션으로 흘려보냅니다. */
async function pipeCapture(media, sessionId) {
  stopCapture();
  const ctx = new AudioContext({ sampleRate: 16000 });
  // 크롬의 자동재생 정책 때문에 새 AudioContext는 suspended로 태어납니다.
  // 그 상태에서는 워클릿이 한 번도 돌지 않아, 화면은 「받는 중」인데 자막이
  // 한 줄도 늘지 않습니다 -- 아무 데도 오류가 나지 않아 더 나쁩니다.
  if (ctx.state === "suspended") await ctx.resume().catch(() => {});
  await ctx.audioWorklet.addModule("/static/capture-worklet.js");
  // 스테레오를 한 채널로 접는 일은 Web Audio 에 맡깁니다. 손으로 왼쪽만
  // 집으면 오른쪽에 치우친 목소리를 통째로 놓칩니다.
  const node = new AudioWorkletNode(ctx, "mimiwatch-capture", {
    channelCount: 1,
    channelCountMode: "explicit",
    channelInterpretation: "speakers",
  });
  ctx.createMediaStreamSource(media).connect(node);
  // 워클릿이 목적지까지 이어져 있지 않으면 크롬이 아예 돌리지 않습니다.
  // 소리를 되돌려 보내면 안 되므로 볼륨 0인 게인을 하나 끼웁니다.
  const mute = ctx.createGain();
  mute.gain.value = 0;
  node.connect(mute).connect(ctx.destination);

  const cap = { stream: media, ctx, node, buf: [], n: 0, timer: null,
                id: sessionId, sending: false };
  state.capture = cap;
  node.port.onmessage = (e) => { cap.buf.push(e.data); cap.n += e.data.length; };
  // 사용자가 크롬의 「공유 중지」를 누르면 여기로 옵니다.
  media.getAudioTracks()[0].addEventListener("ended", () => {
    // 순서가 중요합니다. stopLive가 알림 칸을 비우므로 그 뒤에 씁니다.
    stopLive();
    showLiveNotice("탭 공유가 끝났습니다. 자막 수신을 멈췄습니다.");
  });

  cap.timer = setInterval(() => flushCapture(cap), INGEST_S * 1000);

  // 소리가 실제로 오는지 확인합니다. 위의 resume()이 막히는 경우가 있고,
  // 그때 조용히 실패하면 사용자는 전사가 느린 것과 구별하지 못합니다.
  setTimeout(() => {
    if (state.capture === cap && !cap.n && !cap.sent) {
      showLiveNotice("탭에서 소리가 오지 않습니다. 그 탭이 재생 중인지, "
                     + "공유할 때 「탭 오디오도 공유」를 켰는지 확인하십시오.");
    }
  }, 4000);
}

async function flushCapture(cap) {
  // 앞선 전송이 아직 안 끝났으면 이번 것은 다음 차례에 같이 보냅니다.
  // 겹쳐 보내면 서버 쪽 큐에 순서가 뒤집힌 채로 들어갑니다.
  if (cap.sending || !cap.n || state.capture !== cap) return;
  const frames = cap.buf; const total = cap.n;
  cap.buf = []; cap.n = 0;
  cap.sent = (cap.sent || 0) + total;

  const pcm = new Int16Array(total);
  let i = 0;
  for (const f of frames) {
    for (let k = 0; k < f.length; k++) {
      const v = f[k];
      // 클리핑을 먼저 합니다. 넘친 값을 그대로 곱하면 int16에서 감싸돌아
      // 큰 소리가 잡음으로 바뀝니다.
      pcm[i++] = v < -1 ? -32768 : v > 1 ? 32767 : Math.round(v * 32767);
    }
  }

  cap.sending = true;
  try {
    const res = await fetch("/api/ingest/" + encodeURIComponent(cap.id), {
      method: "POST", headers: { "Content-Type": "application/octet-stream" },
      body: pcm.buffer,
    });
    const st = await res.json();
    if (st.error) {
      // 세션이 없어졌습니다. 아무도 듣지 않는 소리를 계속 올릴 이유가 없습니다.
      showLiveNotice("자막 세션이 끝났습니다: " + st.error);
      stopCapture();
      return;
    }
    if (st.dropped_s >= 1 && st.dropped_s !== cap.warned) {
      cap.warned = st.dropped_s;
      showLiveNotice(`전사가 실시간을 따라가지 못해 ${Math.round(st.dropped_s)}초를 `
                 + "버렸습니다. 가벼운 전사 엔진으로 바꿔 보십시오.");
    }
  } catch (_) {
    // 서버가 잠깐 못 받았습니다. 이 덩어리는 잃지만 다음 것은 갑니다.
  } finally {
    cap.sending = false;
  }
}

/* 「＋ 추가」에서 소리 출처를 「이 브라우저의 다른 탭」으로 고르면 여기로
 * 옵니다. 주소가 아니라 탭이 대상이므로 probe도, yt-dlp도 없습니다. */
async function startTabCapture(title, lang) {
  // 대본 창을 먼저 잡습니다. 이 흐름에는 붙일 영상이 없어서 본 화면에
  // 남겨 둘 이유가 없는데, 공유 창을 고르고 난 뒤에는 팝업이 막힙니다.
  // 아래 requestTabAudio 보다 앞이어야 하는 이유가 그것뿐입니다.
  const pending = openPendingScriptWindow();
  state.scriptWin = pending;

  // 공유 창. 여기서 취소하면 아무 일도 일어나지 않습니다.
  const media = await requestTabAudio();
  if (!media) { if (pending) pending.close(); state.scriptWin = null; return; }
  stopLive();
  // 이름을 적었으면 그것을 씁니다. 비웠으면 고른 탭의 제목을 가져옵니다.
  const name = title || tabTitleFrom(media);
  const res = await (await fetch("/api/live/capture", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: name, lang, viewer_lang: $("viewer-lang").value, backend: state.backend,
      asr: state.asr, refine: state.refine, genre: currentGenre(),
      profile: document.querySelector('#add-form select[name="profile"]').value,
    }),
  })).json();
  if (res.error) {
    media.getTracks().forEach(t => t.stop());
    if (pending) pending.close();
    state.scriptWin = null;
    jobError(res.error);
    return;
  }

  const probe = { id: "", title: name || "탭 오디오", is_live: true };
  state.doc = { id: probe.id, title: probe.title, source_lang: lang || "",
                viewer_lang: $("viewer-lang").value, translated: false,
                backends_done: [state.backend], live: true };
  state.cues = [];
  state.idx = -1;
  state.live = { id: res.id, byId: new Map(), es: null, speakers: new Set(),
                 url: "", lang, probe, asr: state.asr, source: "tab" };
  buildScript();
  renderBackendPicker();
  applyModeForDoc();
  syncRenameButton();
  addLiveToPicker(probe, res.id);
  $("job").hidden = true;
  state.jobId = null;
  $("live-badge").hidden = false;
  // 붙일 영상이 없으므로 오프셋 슬라이더는 의미가 없습니다.
  $("offset-wrap").style.display = "none";
  await attachLive(res.id, "");
  await pipeCapture(media, res.id);
  tabStageNotice(isTabSurface(media) ? "" :
    // 창이나 화면 전체도 소리가 오면 받습니다. 다만 무엇이 섞여 들어올지
    // 알 수 없으므로, 그렇게 골랐다는 것만 짚어 둡니다.
    "지금은 탭이 아니라 창·화면을 공유하고 있습니다 — 다른 소리가 섞이면 "
    + "탭으로 다시 고르십시오.");
  aimScriptWindow(pending, res.id);
}

/* 잡아 둔 빈 창을 대본으로 돌립니다. 팝업이 막혀 못 잡았으면 위쪽 막대로
 * 알립니다 -- 「⧉ 대본 창」을 누르는 것은 새 조작이므로 그때는 열립니다.
 * 그 한 줄은 눌러야 할 것을 알리는 말이라 막대에 남깁니다. */
function aimScriptWindow(pending, sessionId) {
  if (pending && !pending.closed) {
    pending.location = `/?script=${encodeURIComponent("live:" + sessionId)}`;
    hideLiveNotice();
    return;
  }
  showLiveNotice("팝업이 막혀 자막 내역 창을 띄우지 못했습니다 — "
                 + "오른쪽 「⧉ 따로 띄우기」로 여십시오.");
}
