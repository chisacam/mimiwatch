/* 영상 위에 얹는 자막. 페이지와 확장이 함께 쓰는 한 벌입니다.
 *
 * 왜 뽑아 냈는가. 브라우저 확장은 유튜브 페이지 **자체**에 자막을 얹습니다.
 * 우리 페이지의 iframe 위가 아니라요. 그러면 자막을 그리는 코드가 두 군데가
 * 되는데, 이 저장소는 그 값을 이미 여러 번 치렀습니다 -- 스크립트 줄을
 * 그리는 코드가 두 벌이라 화자 표시가 한쪽에만 있었고, 탭 세션 안내를
 * 그리는 곳이 한 군데뿐이라 이어받기에서 사라졌습니다. 자막 모양·자리·끌기는
 * 앞으로도 계속 손볼 것이므로 처음부터 한 벌로 둡니다.
 *
 * 이 모듈이 아는 것은 자막과 상자뿐입니다. 목록도, 스크립트 패널도, SSE도,
 * 엔진 설정도 모릅니다. 그것들은 부르는 쪽의 일입니다.
 *
 *     const ov = MimiOverlay.attach({ overlay, box });
 *     ov.setData({ cues, backend, live, receiving, speakers });
 *     ov.setView({ mode: "both", showPrev: true });
 *     ov.render(currentTime);        // -> 지금 줄의 위치, 없으면 -1
 *
 * 브라우저에서 <script> 로 그냥 읽습니다. 확장의 content script 도 같은
 * 파일을 읽으므로 모듈 문법을 쓰지 않습니다.
 */
(function (root) {
  "use strict";

  /* 자막 자리는 픽셀이 아니라 **상자에 대한 비율**로 적어 둡니다.
   * 창과 전체화면은 크기가 다르고 창 자체도 늘었다 줄었다 하므로, 픽셀로
   * 적어 두면 창에서 정한 자리가 전체화면에서는 왼쪽 위 구석이 됩니다.
   *   x -- 상자 왼쪽에서 잰, 자막 덩어리 **가운데**의 가로 비율
   *   y -- 상자 **바닥**에서 잰, 자막 덩어리 아래끝의 세로 비율
   * y를 위가 아니라 바닥에서 재는 것은, 자막이 붙어 있어야 하는 쪽이
   * 바닥이기 때문입니다. 글자 크기를 키워도 아래끝은 제자리에 남습니다. */
  const DEFAULT_POS = { x: 0.5, y: 0.06 };

  /* 자막 한 줄이 제 끝 시각을 지나도 이만큼은 더 붙잡습니다. 문장 뒤의
   * 자연스러운 쉼에서 자막이 깜빡 사라지면 놓친 것처럼 읽힙니다. */
  const HOLD_S = 1.2;
  /* 받는 중에 마지막 줄을 붙잡아 두는 한계. 이보다 오래되면 내립니다 --
   * 방송이 조용해졌는데 옛 자막이 화면에 남아 있으면 지금 말한 것처럼
   * 보입니다. */
  const LIVE_STALE_S = 20;

  function attach(opts) {
    const overlay = opts.overlay;
    const boxOf = typeof opts.box === "function" ? opts.box : () => opts.box;
    const el = {
      prev: overlay.querySelector(".cue-prev"),
      main: overlay.querySelector(".cue-main"),
      src: overlay.querySelector(".cue-src"),
    };

    const st = {
      cues: [], backend: "", live: false, receiving: false, speakers: false,
      mode: "both", showPrev: true,
      idx: -1, px: 30, scale: 1, pos: Object.assign({}, DEFAULT_POS),
    };
    let drag = null;
    const api = {};

    /* ---------- 어느 줄인가 ---------- */

    /* 자막은 정렬되어 있고 겹치지 않으므로, 이분 탐색보다 걸어가는 쪽이
     * 낫습니다 -- 재생은 한 번에 0.1초쯤 나아가고 거의 언제나 같은 줄이나
     * 바로 다음 줄에 떨어집니다. */
    api.cueAt = function (t) {
      const c = st.cues;
      if (!c.length) return -1;

      // 라이브 자막은 녹화본처럼 찾을 수 없습니다. 한 줄이 나오기까지 몇 초가
      // 걸리므로, 그 줄이 생겼을 때 플레이어는 이미 그 시각을 지나 있습니다.
      // 시각으로 맞추면 언제나 아무것도 보이지 않습니다. 그래서 가장 최근에
      // 알아들은 줄을 다음 줄이 올 때까지 붙잡습니다. 라이브 자막은 원래
      // 그렇게 읽습니다. 오프셋은 여전히 트랙 전체를 밉니다.
      //
      // **받는 중일 때만** 이 규칙입니다. 방송이 끝나 녹화본이 되면 아래의
      // 시각 기반 조회로 갑니다 -- 그렇게 하지 않으면 마지막 줄이 20초 지난
      // 뒤로는 어느 자리에서도 자막이 뜨지 않습니다.
      if (st.live && st.receiving) {
        const i = c.length - 1;
        const age = (Date.now() - (c[i].arrived || 0)) / 1000;
        return age > LIVE_STALE_S ? -1 : i;
      }

      let i = st.idx >= 0 && st.idx < c.length ? st.idx : 0;
      while (i > 0 && startOf(c[i]) > t) i--;
      while (i < c.length - 1 && startOf(c[i + 1]) <= t) i++;
      if (t < startOf(c[i])) return -1;
      if (t > endOf(c[i]) + HOLD_S) return -1;
      return i;
    };

    const startOf = (c) => (c.start != null ? c.start : c.t) || 0;
    const endOf = (c) => (c.end != null && c.end > startOf(c)
                          ? c.end : startOf(c) + 6);

    /* ---------- 그리기 ---------- */

    const trOf = (c) => (c && c.translations ? c.translations[st.backend] : null);
    const chipFor = (c) => (st.speakers && c && c.speaker) ? c.speaker : "";

    // 번역이 없는 줄(모델이 건드려 봐야 망칠 조각)은 원문 쪽입니다.
    // 「번역만」이나 「둘 다」 안에서 보여 주면, 애초에 필요 없던 줄이 아니라
    // 번역에 실패한 줄처럼 읽힙니다.
    function lineOf(c) {
      if (!c) return "";
      if (st.mode === "source") return c.text;
      if (st.mode === "off") return "";
      return trOf(c) || "";
    }

    function put(node, text, speaker) {
      if (!node) return;
      node.textContent = "";
      if (!text) return;
      const s = document.createElement("span");
      if (speaker) {
        const chip = document.createElement("b");
        chip.className = "spk-chip";
        chip.textContent = speaker;
        s.appendChild(chip);
      }
      s.appendChild(document.createTextNode(text));
      node.appendChild(s);
    }

    /* `t` 는 이미 오프셋이 더해진 재생 위치입니다. 돌려주는 것은 지금 줄의
     * 자리이고, 없으면 -1 입니다. 바뀌었는지는 부르는 쪽이 압니다. */
    api.render = function (t) {
      const i = api.cueAt(t);
      const cur = i >= 0 ? st.cues[i] : null;
      const prev = i > 0 ? st.cues[i - 1] : null;
      put(el.main, lineOf(cur), chipFor(cur));
      put(el.src, st.mode === "both" && trOf(cur) ? cur.text : "");
      put(el.prev, st.showPrev ? lineOf(prev) : "", chipFor(prev));
      // 문장이 바뀌면 덩어리 크기도 바뀝니다. 짧은 문장 자리에 놓아 둔 자막이
      // 긴 문장에서 상자 밖으로 나가지 않도록, 그릴 때마다 자리를 다시
      // 자릅니다.
      api.reflow();
      st.idx = i;
      return i;
    };

    api.clear = function () {
      put(el.main, ""); put(el.src, ""); put(el.prev, "");
      st.idx = -1;
    };

    /* ---------- 값 넣기 ---------- */

    api.setData = function (d) {
      if (d.cues) { st.cues = d.cues; st.idx = -1; }
      if (d.backend !== undefined) st.backend = d.backend;
      if (d.live !== undefined) st.live = !!d.live;
      if (d.receiving !== undefined) st.receiving = !!d.receiving;
      if (d.speakers !== undefined) st.speakers = !!d.speakers;
    };

    api.setView = function (v) {
      if (v.mode !== undefined) st.mode = v.mode;
      if (v.showPrev !== undefined) st.showPrev = !!v.showPrev;
    };

    api.index = () => st.idx;
    api.resetIndex = () => { st.idx = -1; };

    /* 글자 크기는 고른 값 그대로가 아니라 **상자가 커진 만큼** 키웁니다.
     * 창에서 고른 30px을 전체화면에서 그대로 쓰면 화면이 서너 배 커진 만큼
     * 자막만 작아 보입니다. 배율은 부르는 쪽이 정합니다 -- 무엇을 기준으로
     * 삼을지는 페이지와 확장이 다릅니다. */
    api.setSize = function (px, scale) {
      if (px != null) st.px = px;
      if (scale != null) st.scale = scale;
      const eff = Math.round(st.px * (st.scale || 1));
      overlay.style.fontSize = eff + "px";
      if (el.main) el.main.style.fontSize = eff + "px";
      api.reflow();
      return eff;
    };

    /* ---------- 자리 ---------- */

    api.pos = () => Object.assign({}, st.pos);

    api.setPos = function (p) {
      st.pos = { x: p && p.x != null ? p.x : DEFAULT_POS.x,
                 y: p && p.y != null ? p.y : DEFAULT_POS.y };
      api.reflow();
    };

    api.resetPos = function () {
      st.pos = Object.assign({}, DEFAULT_POS);
      api.reflow();
      if (api.onPos) api.onPos(api.pos());
    };

    /* 저장된 비율을 실제 left/bottom 으로 앉힙니다. */
    api.reflow = function () {
      const p = clamp(st.pos);
      overlay.style.left = (p.x * 100).toFixed(3) + "%";
      overlay.style.bottom = (p.y * 100).toFixed(3) + "%";
    };

    /* 저장값을 그대로 쓰지 않고 그릴 때마다 한 번 더 자릅니다.
     *
     * 자막 덩어리의 크기는 글자 크기와 그때 나온 문장 길이에 따라 매번
     * 달라집니다. 놓을 때는 상자 안이었어도 다음 문장이 길면 밖으로 삐져나가
     * 글자가 잘립니다. 자르는 것은 **보여 주는 값**뿐입니다 -- 저장값까지
     * 같이 줄이면 짧은 문장이 한 번 지나갈 때마다 사용자가 정한 자리가
     * 조금씩 안쪽으로 끌려옵니다. */
    function clamp(p) {
      const box = boxOf();
      if (!box) return p;
      const bw = box.clientWidth, bh = box.clientHeight;
      if (!bw || !bh) return p;
      // 가운데를 기준점으로 잡았으니 좌우 여유는 덩어리 폭의 절반입니다.
      // 자막이 상자보다 넓으면(아주 큰 글자 + 긴 문장) 여유가 없으므로
      // 가운데로 둡니다.
      const half = Math.min(overlay.offsetWidth / 2 / bw, 0.5);
      const top = Math.max(0, 1 - overlay.offsetHeight / bh);
      return {
        x: Math.min(Math.max(p.x, half), 1 - half),
        y: Math.min(Math.max(p.y, 0), top),
      };
    }
    api.clamp = clamp;

    /* ---------- 끌기 ----------
     *
     * setPointerCapture 가 있어야 합니다. 포인터가 자막 밖으로 -- 곧 영상
     * 위로 -- 나가는 순간 그 움직임은 그쪽 문서의 것이 되어 우리에게 오지
     * 않고, 교차 출처면 document 에 mousemove 를 걸어도 넘어오지 않습니다.
     * 캡처를 걸어 두면 뗄 때까지 모든 이동이 이 요소로 옵니다. 마우스와
     * 터치를 한 벌로 다룰 수 있는 것은 덤입니다. */
    function down(e) {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const r = overlay.getBoundingClientRect();
      drag = {
        id: e.pointerId,
        // 잡은 지점과 기준점(가운데-아래끝)의 어긋남. 빼 주지 않으면 누르는
        // 순간 자막이 커서 밑으로 홱 옮겨 붙습니다.
        dx: e.clientX - (r.left + r.width / 2),
        dy: e.clientY - r.bottom,
      };
      overlay.setPointerCapture(e.pointerId);
      overlay.classList.add("dragging");
      e.preventDefault();
    }

    function move(e) {
      if (!drag || e.pointerId !== drag.id) return;
      // 전체화면에서도 그대로 통합니다. clientX/Y도 이 사각형도 뷰포트
      // 기준이라, 상자가 화면 전체가 되면 두 값이 함께 커집니다.
      const box = boxOf();
      if (!box) return;
      const r = box.getBoundingClientRect();
      if (!r.width || !r.height) return;
      st.pos = {
        x: (e.clientX - drag.dx - r.left) / r.width,
        y: (r.bottom - (e.clientY - drag.dy)) / r.height,
      };
      api.reflow();
    }

    function up(e) {
      if (!drag || e.pointerId !== drag.id) return;
      overlay.classList.remove("dragging");
      drag = null;
      // 놓은 자리는 **잘라서** 저장합니다. 상자 한참 밖에서 손을 뗐는데 그
      // 값을 그대로 남기면, 다음에 열 때 화면에 없는 자리가 되살아납니다.
      st.pos = clamp(st.pos);
      if (api.onPos) api.onPos(api.pos());
    }

    overlay.addEventListener("pointerdown", down);
    overlay.addEventListener("pointermove", move);
    overlay.addEventListener("pointerup", up);
    overlay.addEventListener("pointercancel", up);

    api.destroy = function () {
      overlay.removeEventListener("pointerdown", down);
      overlay.removeEventListener("pointermove", move);
      overlay.removeEventListener("pointerup", up);
      overlay.removeEventListener("pointercancel", up);
    };

    return api;
  }

  root.MimiOverlay = { attach, DEFAULT_POS, HOLD_S, LIVE_STALE_S };
})(typeof window !== "undefined" ? window : globalThis);
