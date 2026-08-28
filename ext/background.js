/* 서버와 이야기하는 쪽. content script 를 대신해 localhost 로 나갑니다.
 *
 * **왜 여기서 하는가.** content script 의 fetch 는 그 페이지(youtube.com)의
 * 출처로 나갑니다. 그러면 우리 서버가 `Access-Control-Allow-Origin` 으로
 * youtube.com 을 허락해야 하는데, 그 순간 유튜브 페이지에서 도는 모든
 * 스크립트가 우리 서버의 쓰기 API에 닿을 수 있게 됩니다. 서비스 워커의
 * fetch 는 확장의 출처로 나가고 `host_permissions` 가 CORS 를 건너뛰므로,
 * 서버는 아무것도 열어 줄 필요가 없습니다.
 *
 * **EventSource 를 쓰지 않습니다.** MV3 서비스 워커에는 없습니다. 대신
 * fetch 의 몸통을 흘려 읽으며 SSE 를 직접 풉니다 -- 형식이 단순하고
 * (`data: ...\n\n`), 다시 붙는 규칙을 우리가 정할 수 있어 오히려 낫습니다.
 *
 * **왜 워커가 안 죽는가.** MV3 서비스 워커는 가만히 두면 30초쯤 뒤에
 * 내려갑니다. 포트가 연결되어 있고 그 위로 메시지가 오가면 그 시계가
 * 다시 돕니다. 자막은 몇 초에 한 줄씩 오고, 조용한 동안에는 아래
 * KEEPALIVE_MS 마다 한 번 찔러 둡니다.
 */

const BASE_KEY = "serverBase";
const DEFAULT_BASE = "http://localhost:8900";
const KEEPALIVE_MS = 20000;
// 끊겼을 때 다시 붙기까지. 서버를 재시작하는 동안 몇 번 실패하는 것이
// 정상이므로 조용히 기다립니다.
const RETRY_MS = 3000;

async function base() {
  const got = await chrome.storage.local.get(BASE_KEY);
  return got[BASE_KEY] || DEFAULT_BASE;
}

async function api(path, init) {
  const res = await fetch((await base()) + path, init);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/* ---------- 팝업과 content script 가 물어보는 것 ---------- */

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  // offscreen 문서로 가는 것은 우리 것이 아닙니다. chrome.runtime.sendMessage 는
  // 확장 안의 **모든** 수신자에게 갑니다. 이 자리에서 「모르는 요청」이라고
  // 답해 버리면 offscreen 의 진짜 답과 경주가 되고, 먼저 닿는 쪽이 이깁니다.
  if (msg && msg.target === "offscreen") return;
  (async () => {
    try {
      if (msg.type === "sessions") reply({ ok: true, data: await api("/api/live/sessions") });
      else if (msg.type === "videos") reply({ ok: true, data: await api("/api/videos") });
      else if (msg.type === "backends") reply({ ok: true, data: await api("/api/backends") });
      else if (msg.type === "base") reply({ ok: true, data: await base() });
      else if (msg.type === "setBase") {
        await chrome.storage.local.set({ [BASE_KEY]: msg.base });
        reply({ ok: true });
      } else if (msg.type === "watch") {
        reply({ ok: true, ...(await setWatch(msg.tabId, msg.value || "")) });
      } else if (msg.type === "whatToWatch") {
        // content script 가 방금 떠서 스스로 묻습니다. 자기 탭 번호는
        // 모르지만 우리는 sender 로 압니다.
        const id = sender && sender.tab && sender.tab.id;
        const k = "tab:" + id;
        reply({ ok: true, data: id ? (await chrome.storage.local.get(k))[k] || "" : "" });
      } else if (msg.type === "watching") {
        const k = "tab:" + msg.tabId;
        reply({ ok: true, data: (await chrome.storage.local.get(k))[k] || "" });
      } else if (msg.type === "startUrl") {
        reply(await startFromUrl(msg));
      } else if (msg.type === "startCapture") {
        reply(await startFromTab(msg));
      } else if (msg.type === "stopSession") {
        await stopCapture();
        if (msg.sessionId) await post("/api/live/stop", { id: msg.sessionId });
        reply({ ok: true });
      } else reply({ ok: false, error: "모르는 요청: " + msg.type });
    } catch (e) {
      reply({ ok: false, error: String((e && e.message) || e) });
    }
  })();
  return true;            // 비동기로 답합니다
});

/* 이 탭이 무엇을 볼지 정하고, 그것을 content script 에 알립니다.
 *
 * **닿았는지 확인합니다.** content script 가 없을 수 있습니다 -- 확장을 다시
 * 로드한 직후(열려 있던 탭은 옛 것을 계속 씁니다), 유튜브가 아닌 탭, 방금
 * 열려 아직 뜨지 않은 탭. 예전에는 조용히 실패하고 끝이라, 사용자에게는
 * 「골랐는데 아무 일도 안 난다」로 보였고 페이지를 새로고침해야 나왔습니다.
 *
 * 닿지 않았으면 우리가 새로고침합니다. 저장은 이미 되어 있으므로 새로 뜬
 * content script 가 스스로 읽어 갑니다(whatToWatch). **닿았으면 하지
 * 않습니다** -- 보고 있던 자리가 튀는 것은 그 자체로 손해입니다. */
async function setWatch(tabId, value, opts) {
  await chrome.storage.local.set({ ["tab:" + tabId]: value });
  try {
    const r = await chrome.tabs.sendMessage(
      tabId, value ? { type: "attach", value } : { type: "detach" });
    if (r && r.ok) return { delivered: true };
  } catch (_) { /* 아래에서 다룹니다 */ }
  if (!value) return { delivered: false };   // 내리는 것은 새로고침할 일이 아닙니다
  // 탭 소리를 잡는 중에는 새로고침하지 않습니다. 잡아 둔 스트림은 그 탭에
  // 매여 있어서, 새로고침하면 방금 시작한 받아 적기가 끊깁니다. 자막은
  // 곧 오는데 화면에만 안 붙는 것과, 받는 것 자체가 끊기는 것은 다른
  // 이야기입니다.
  if (opts && opts.noReload) return { delivered: false, skippedReload: true };
  try {
    await chrome.tabs.reload(tabId);
    return { delivered: false, reloaded: true };
  } catch (e) {
    return { delivered: false, error: String(e.message || e) };
  }
}

/* ---------- 세션 시작 ---------- */

/* 주소로 시작합니다. 서버가 yt-dlp 로 직접 받으므로 브라우저를 닫아도
 * 계속 받아 적습니다. 멤버십 전용 방송은 이 길로 받지 못합니다. */
async function startFromUrl(msg) {
  const probe = await post("/api/probe", { url: msg.url });
  if (probe.error) return { ok: false, error: probe.error };
  if (!probe.is_live) {
    return { ok: false, error: "라이브가 아닙니다. 녹화본은 mimiwatch 페이지에서 추가하십시오." };
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

/* 이 탭에서 나는 소리로 시작합니다. 멤버십 전용 방송처럼 서버가 받을 수
 * 없는 것을 위한 길입니다.
 *
 * 소리를 실제로 잡는 일은 offscreen 문서가 합니다 -- 서비스 워커에는
 * getUserMedia 도 AudioContext 도 없습니다. */
async function startFromTab(msg) {
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: msg.tabId });
  const cfg = await api("/api/backends");
  const res = await post("/api/live/capture", {
    title: msg.title || "", lang: msg.lang || null,
    viewer_lang: msg.viewerLang || "ko",
    backend: cfg.active, asr: cfg.asr_active, refine: !!msg.refine,
    genre: msg.genre || "general", profile: msg.profile || "broadcast",
  });
  if (res.error) return { ok: false, error: res.error };
  await ensureOffscreen();
  const started = await chrome.runtime.sendMessage({
    target: "offscreen", type: "capture",
    streamId, sessionId: res.id, base: await base(),
  });
  if (!started || !started.ok) {
    await post("/api/live/stop", { id: res.id });
    return { ok: false, error: (started && started.error) || "소리를 잡지 못했습니다" };
  }
  const w = await setWatch(msg.tabId, "live:" + res.id, { noReload: true });
  return { ok: true, id: res.id, ...w };
}

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument();
  if (has) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "탭에서 나는 소리를 받아 로컬 mimiwatch 서버로 보냅니다.",
  });
}

async function stopCapture() {
  if (await chrome.offscreen.hasDocument()) {
    try { await chrome.runtime.sendMessage({ target: "offscreen", type: "stop" }); }
    catch (_) { /* 이미 내려갔습니다 */ }
  }
}

/* ---------- 자막을 흘려보내는 통로 ---------- */

/* SSE 를 직접 풉니다. 서버가 보내는 것은 `data: {...}` 한 줄과 빈 줄뿐이라
 * 규격 전체를 다룰 필요가 없습니다. */
async function pump(url, onEvent, signal) {
  const res = await fetch(url, { signal });
  if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let cut;
    // 이벤트 하나는 빈 줄로 끝납니다. 서버가 조각내어 보낼 수 있으므로
    // 완전한 덩어리가 모일 때까지 들고 있습니다.
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data:")) continue;   // `: keepalive` 는 흘립니다
        try { onEvent(JSON.parse(line.slice(5).trim())); }
        catch (_) { /* 형식이 깨진 프레임은 버립니다 */ }
      }
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
      // 조용한 동안 워커를 살려 둡니다. 포트 위의 메시지가 유휴 시계를
      // 다시 돌립니다.
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

    // 라이브는 끊길 수 있습니다 -- 서버 재시작, 방송 종료, 잠자기. 조용히
    // 다시 붙습니다. 서버가 쌓인 자막을 접속 직후에 다시 보내 주므로
    // 되붙어도 빠지는 줄이 없습니다.
    while (!closed) {
      abort = new AbortController();
      try {
        await pump(`${b}/api/live/events/${encodeURIComponent(sid)}`,
                   (e) => { if (!closed) port.postMessage({ type: "event", data: e }); },
                   abort.signal);
        if (closed) return;
        // 끝난 세션은 서버가 백로그를 다 보내고 닫습니다. 정상 종료입니다.
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
