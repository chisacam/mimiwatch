/* 채팅 자리에 대본을 세웁니다.
 *
 * **왜 mimiwatch 의 대본 창을 액자로 끼우지 않았는가.** 그러려고 했습니다 --
 * 이미 만들어 둔 `?script=…` 화면을 그대로 쓰면 한 줄도 새로 지을 필요가
 * 없으니까요. 유튜브의 CSP 에는 frame-src 도 default-src 도 없어서 막지
 * 않는데, 정작 https 페이지가 http 를 액자로 끼우는 것이 혼합 콘텐츠로
 * 막힙니다. `127.0.0.1` 과 `localhost` 둘 다 about:blank 로 남았습니다.
 * 그래서 여기서 직접 그립니다.
 *
 * 읽기 전용입니다. 고치기·재번역·내보내기는 mimiwatch 페이지에 있습니다 --
 * 여기까지 옮기면 두 벌이 되고, 이 자리에서 필요한 것은 읽는 일입니다.
 */
(function (root) {
  "use strict";

  const ID = "mimiwatch-panel";
  // 유튜브의 오른쪽 열. 라이브면 채팅이, 아니면 관련 영상이 들어 있습니다.
  const findColumn = () => document.querySelector("#secondary-inner") ||
                           document.querySelector("#secondary");
  const findChat = () => document.querySelector("ytd-live-chat-frame#chat") ||
                         document.querySelector("#chat");

  /* 작은 도우미. createElement + className + textContent 를 한 줄로. */
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  let node = null, list = null, head = null;
  let rows = new Map();          // 자막 번호 → 줄 요소
  let hidden = null;             // 우리가 감춘 채팅. 되돌릴 때 씁니다.
  let onSeek = null;
  let follow = true;

  /* 문서에 남은 우리 것을 **전부** 걷어 냅니다.
   *
   * 하나만 추적하면 놓칩니다. 유튜브는 화면을 갈아 끼우며 오른쪽 열을
   * 통째로 떼었다 다시 붙이는데, 떼여 있는 동안 `node.isConnected` 가
   * false 라 새 것을 하나 더 만들고, 옛 것이 되붙으면 그때부터 둘이 됩니다.
   * 추적하는 것은 새 것뿐이라 옛 것은 체크를 꺼도 사라지지 않았습니다. */
  function sweep() {
    document.querySelectorAll("#" + ID).forEach((e) => e.remove());
  }

  function mount() {
    const col = findColumn();
    if (!col) return false;
    if (node && node.isConnected && node.parentElement === col) return true;
    sweep();

    node = document.createElement("div");
    node.id = ID;
    node.className = "mw-panel";
    head = document.createElement("div");
    head.className = "mw-panel-head";
    // innerHTML 을 쓰지 않습니다. 유튜브는 Trusted Types 를 켜 두었고
    // (`require-trusted-types-for 'script'`), 그 문서에서 innerHTML 에
    // 문자열을 넣으면 거부됩니다. content script 가 면제되는지는 크롬 판에
    // 따라 다르므로 아예 기대지 않습니다.
    head.append(el("b", "", "자막 내역"), el("span", "mw-count", "0줄"));
    const foll = el("label", "mw-follow");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = true;
    foll.append(box, document.createTextNode(" 따라가기"));
    head.appendChild(foll);
    list = document.createElement("div");
    list.className = "mw-panel-list";
    node.append(head, list);

    // 채팅이 있으면 그 자리를 대신 씁니다. 나란히 두면 둘 다 좁아져서
    // 어느 쪽도 읽을 수 없습니다.
    const chat = findChat();
    if (chat && chat.style.display !== "none") {
      hidden = chat;
      // 채팅의 높이를 물려받습니다. 그 자리에 들어가는 것이므로 크기도
      // 그 자리의 것이어야 어색하지 않습니다.
      const h = chat.getBoundingClientRect().height;
      if (h > 200) node.style.height = Math.round(h) + "px";
      chat.style.display = "none";
    }
    col.insertBefore(node, col.firstChild);

    head.querySelector("input").addEventListener("change", (e) => {
      follow = e.target.checked;
      if (follow) pin();
    });
    // 사용자가 위로 올려 읽기 시작하면 따라가기를 멈춥니다. 읽는 중에
    // 바닥으로 끌려 내려가면 그 줄을 다시 찾아야 합니다.
    list.addEventListener("scroll", () => {
      const atEnd = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
      if (atEnd !== follow) {
        follow = atEnd;
        head.querySelector("input").checked = atEnd;
      }
    });
    return true;
  }

  function unmount() {
    sweep();
    if (hidden) { hidden.style.display = ""; hidden = null; }
    // 우리가 감춘 것이 아니더라도, 우리 때문에 감춰진 채 남은 채팅은
    // 되돌려 놓습니다. 화면이 갈아 끼워지면 hidden 이 옛 요소를 가리키게
    // 되어 진짜 채팅이 감춰진 채로 남습니다.
    const chat = findChat();
    if (chat && chat.style.display === "none") chat.style.display = "";
    node = list = head = null;
    rows = new Map();
  }

  function pin() {
    if (list && follow) list.scrollTop = list.scrollHeight;
  }

  const fmt = (s) => {
    const t = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), x = t % 60;
    const two = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${two(m)}:${two(x)}` : `${two(m)}:${two(x)}`;
  };

  function rowFor(c) {
    let row = rows.get(c.id);
    if (!row) {
      row = document.createElement("div");
      row.className = "mw-line";
      const body = document.createElement("div");
      body.append(el("div", "mw-tx"), el("div", "mw-tr"));
      row.append(el("div", "mw-t"), body);
      // 대본에서 누르면 그 지점으로. 여기서는 진짜 <video> 를 잡을 수 있어
      // 그냥 됩니다 -- mimiwatch 페이지의 대본 창에서는 붙일 영상이 없어
      // 하지 못하는 일입니다.
      row.addEventListener("click", () => {
        if (onSeek) onSeek((c.start != null ? c.start : c.t) || 0);
      });
      rows.set(c.id, row);
      list.appendChild(row);
    }
    return row;
  }

  /* 자막을 그립니다. 통째로 다시 그리지 않고 바뀐 줄만 손봅니다 -- 라이브는
   * 몇 초에 한 줄씩 오는데 그때마다 수백 줄을 다시 지으면 읽던 자리가
   * 흔들립니다. */
  function render(cues, opts) {
    if (!node) return;
    const trKey = (opts && opts.trKey) || "_";
    const alive = new Set();
    for (const c of cues) {
      alive.add(c.id);
      const row = rowFor(c);
      const t = row.querySelector(".mw-t");
      const tx = row.querySelector(".mw-tx");
      const tr = row.querySelector(".mw-tr");
      const time = fmt((c.start != null ? c.start : c.t) || 0);
      const trText = (c.translations && c.translations[trKey]) || "";
      if (t.textContent !== time) t.textContent = time;
      if (tx.textContent !== c.text) tx.textContent = c.text;
      if (tr.textContent !== trText) tr.textContent = trText;
      row.classList.toggle("mw-note", c.kind === "note");
    }
    // 정제본이 흡수한 줄, 지운 줄.
    for (const [id, row] of rows) {
      if (!alive.has(id)) { row.remove(); rows.delete(id); }
    }
    head.querySelector(".mw-count").textContent = cues.length + "줄";
    pin();
  }

  root.MimiPanel = {
    mount, unmount, render,
    mounted: () => !!(node && node.isConnected),
    setSeek: (fn) => { onSeek = fn; },
    // 유튜브가 화면을 갈아 끼우면 우리가 넣은 것이 사라집니다. 그때 다시
    // 세우려면 줄 지도도 비워야 합니다 -- 옛 요소를 가리키고 있습니다.
    reset: () => { rows = new Map(); },
  };
})(typeof window !== "undefined" ? window : globalThis);
