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
/* 그래프·int16 변환·2초마다 올리기는 확장과 공유하는 web/capture.js
 * (MimiCapture)가 합니다. 여기서는 스트림을 얻는 일과 화면 알림만 맡습니다. */
function stopCapture() {
  const c = state.capture;
  state.captureSession = null;
  if (!c) return;
  state.capture = null;
  MimiCapture.stop(c);
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
    jobError(t("capture.error.getAudio", { error: (err && err.message) || err }));
    return null;
  }
  if (!media.getAudioTracks().length) {
    media.getTracks().forEach(t => t.stop());
    jobError(t("capture.error.noAudio"));
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
function tabStageNotice(tail, tile = focusedTile()) {
  playerError(t("capture.stage.notice") + (tail ? " " + tail : ""), null, tile);
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
  input.value = cur === t("capture.tabAudio") ? "" : cur;
  input.placeholder = t("capture.rename.placeholder");
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
  const cap = await MimiCapture.start({
    media,
    workletUrl: "/static/capture-worklet.js",
    post: (buf) => fetch("/api/ingest/" + encodeURIComponent(sessionId), {
      method: "POST", headers: { "Content-Type": "application/octet-stream" },
      body: buf,
    }).then(r => r.json()),
    // 사용자가 크롬의 「공유 중지」를 누르면 여기로 옵니다. 순서가 중요합니다 --
    // stopLive가 알림 칸을 비우므로 그 뒤에 씁니다.
    onEnded: () => {
      stopLive();
      showLiveNotice(t("capture.notice.sharingEnded"));
    },
    // 세션이 없어졌습니다(서버 재시작 등). 공유는 모듈이 이미 놓았습니다.
    onError: (msg) => {
      state.capture = null;
      showLiveNotice(t("capture.notice.sessionEnded", { error: msg }));
    },
    onDropped: (s) => showLiveNotice(
      t("capture.notice.dropped", { n: Math.round(s) })),
  });
  state.capture = cap;
  state.captureSession = sessionId;     // 어느 세션의 소리인지. 그 세션이 끝날 때만 놓습니다

  // 소리가 실제로 오는지 확인합니다. AudioContext의 resume()이 막히는 경우가
  // 있고, 그때 조용히 실패하면 사용자는 전사가 느린 것과 구별하지 못합니다.
  setTimeout(() => {
    if (state.capture === cap && !cap.n && !cap.sent) {
      showLiveNotice(t("capture.notice.silent"));
    }
  }, 4000);
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

  const probe = { id: "", title: name || MW_I18N.t("capture.tabAudio"), is_live: true };
  const t = soloTile();
  bindLive(t, {
    id: res.id, store: MimiCues.create(), es: null, speakers: new Set(),
    url: "", lang, probe, asr: state.asr, backend: state.backend, source: "tab",
  }, {
    id: probe.id, title: probe.title, source_lang: lang || "",
    viewer_lang: $("viewer-lang").value, translated: false,
    backends_done: [state.backend], live: true,
  });
  t.src = { site: "none" };          // 붙일 영상이 없습니다
  showTileInPanels(t);
  addLiveToPicker(probe, res.id);
  await attachLive(t);
  await pipeCapture(media, res.id);
  tabStageNotice(isTabSurface(media) ? "" :
    // 창이나 화면 전체도 소리가 오면 받습니다. 다만 무엇이 섞여 들어올지
    // 알 수 없으므로, 그렇게 골랐다는 것만 짚어 둡니다.
    //
    // `const t` below shadows the lookup for this whole function body, so the
    // long name is the one that works here.
    MW_I18N.t("capture.stage.windowShare"));
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
  showLiveNotice(t("capture.notice.popupBlocked"));
}
