/* 서버와 이야기하는 쪽. content script 를 대신해 localhost 로 나갑니다.
 *
 * **왜 여기서 하는가.** content script 의 fetch 는 그 페이지(youtube.com)의
 * 출처로 나갑니다. 그러면 우리 서버가 `Access-Control-Allow-Origin` 으로
 * youtube.com 을 허락해야 하는데, 그 순간 유튜브 페이지에서 도는 모든
 * 스크립트가 우리 서버의 쓰기 API에 닿을 수 있게 됩니다. 서비스 워커의
 * fetch 는 확장의 출처로 나가고 `host_permissions` 가 CORS 를 건너뛰므로,
 * 서버는 아무것도 열어 줄 필요가 없습니다.
 *
 * **왜 워커가 안 죽는가.** MV3 서비스 워커는 가만히 두면 30초쯤 뒤에
 * 내려갑니다. 포트가 연결되어 있고 그 위로 메시지가 오가면 그 시계가
 * 다시 돕니다. 자막은 몇 초에 한 줄씩 오고, 조용한 동안에는 아래
 * KEEPALIVE_MS 마다 한 번 찔러 둡니다.
 */

const BASE_KEY = "serverBase";
const DEFAULT_BASE = "http://localhost:8900";
// 자막이 뜸한 동안 포트를 살려 두는 간격. 서비스 워커의 유휴 시계(30초)보다
// 넉넉히 짧아야 합니다.
const KEEPALIVE_MS = 20000;

async function base() {
  const got = await chrome.storage.local.get(BASE_KEY);
  return got[BASE_KEY] || DEFAULT_BASE;
}

async function api(path, init) {
  const res = await fetch((await base()) + path, init);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

/* 팝업과 content script 가 물어보는 것들. */
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    try {
      if (msg.type === "sessions") reply({ ok: true, data: await api("/api/live/sessions") });
      else if (msg.type === "videos") reply({ ok: true, data: await api("/api/videos") });
      else if (msg.type === "base") reply({ ok: true, data: await base() });
      else if (msg.type === "setBase") {
        await chrome.storage.local.set({ [BASE_KEY]: msg.base });
        reply({ ok: true });
      } else if (msg.type === "watch") {
        // 이 탭이 어느 자막을 볼지 정해 둡니다. content script 가 붙을 때
        // 이것을 읽습니다.
        await chrome.storage.local.set({ ["tab:" + msg.tabId]: msg.value || "" });
        if (msg.value) chrome.tabs.sendMessage(msg.tabId, { type: "attach", value: msg.value });
        else chrome.tabs.sendMessage(msg.tabId, { type: "detach" });
        reply({ ok: true });
      } else if (msg.type === "watching") {
        const k = "tab:" + msg.tabId;
        reply({ ok: true, data: (await chrome.storage.local.get(k))[k] || "" });
      } else reply({ ok: false, error: "모르는 요청: " + msg.type });
    } catch (e) {
      reply({ ok: false, error: String(e.message || e) });
    }
  })();
  return true;            // 비동기로 답합니다
});

/* content script 가 자막을 받아 갈 통로. 한 탭에 하나입니다. */
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "cues") return;
  let es = null, timer = null, closed = false;

  const stop = () => {
    closed = true;
    if (timer) clearInterval(timer);
    if (es) { try { es.close(); } catch (_) {} }
    es = null;
  };

  port.onDisconnect.addListener(stop);

  port.onMessage.addListener(async (msg) => {
    if (msg.type !== "attach") return;
    stop();
    closed = false;
    const b = await base();
    const value = msg.value || "";
    const sid = value.startsWith("live:") ? value.slice(5) : "";
    try {
      if (sid) {
        // 라이브는 SSE 로 옵니다. 쌓인 것을 먼저 보내고 이어서 흘려보냅니다.
        es = new EventSource(`${b}/api/live/events/${encodeURIComponent(sid)}`);
        es.onmessage = (ev) => {
          if (closed) return;
          try { port.postMessage({ type: "event", data: JSON.parse(ev.data) }); }
          catch (_) { /* 형식이 깨진 프레임은 버립니다 */ }
        };
        es.onerror = () => { if (!closed) port.postMessage({ type: "stalled" }); };
      } else {
        // 녹화본은 한 번에 다 옵니다.
        const doc = await api(`/api/video/${encodeURIComponent(value)}`);
        port.postMessage({ type: "doc", data: doc });
      }
      timer = setInterval(() => {
        // 조용한 동안 워커를 살려 둡니다. 포트 위의 메시지가 유휴 시계를
        // 다시 돌립니다.
        if (!closed) port.postMessage({ type: "tick" });
      }, KEEPALIVE_MS);
    } catch (e) {
      port.postMessage({ type: "error", error: String(e.message || e) });
    }
  });
});

/* 탭이 사라지면 기억해 둔 것도 지웁니다. */
chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.local.remove("tab:" + tabId);
});
