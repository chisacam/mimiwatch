"""브라우저 확장이 형태를 갖추고 있는지 확인합니다.

확장은 크롬에 직접 얹어 봐야 진짜로 도는지 알 수 있습니다. 그래도 얹기 전에
걸러 낼 수 있는 것들이 있습니다 -- manifest 가 가리키는 파일이 실제로 있는지,
공유하는 자막 모듈이 페이지 쪽과 어긋나지 않았는지.

**자막 모듈은 사본입니다.** content script 는 확장 폴더 안의 파일만 읽을 수
있어서(원격 코드 실행은 MV3 가 막습니다) `web/overlay.js` 를 `ext/` 로
복사해 둡니다. 사본은 언젠가 반드시 어긋나므로 여기서 지킵니다 -- 구현이
두 벌인 것과 기계적인 복사본은 다른 이야기이고, 후자는 시험으로 묶을 수
있습니다.

    .venv/bin/python bench/ext_check.py

어긋났으면 이렇게 맞춥니다:

    cp web/overlay.js ext/overlay.js
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(HERE, "ext")
WEB = os.path.join(HERE, "web")

FAIL = []


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def balanced(src: str) -> bool:
    """따옴표와 주석 밖에서 괄호가 맞는지. 문법 검사는 아니지만, 편집하다
    한쪽을 지워 먹은 것은 여기서 걸립니다."""
    depth = {"{": 0, "(": 0, "[": 0}
    pair = {"}": "{", ")": "(", "]": "["}
    i, n = 0, len(src)
    quote = None
    while i < n:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2; continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            i = src.find("\n", i)
            if i < 0:
                break
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            i = src.find("*/", i)
            if i < 0:
                break
            i += 1
        elif c in depth:
            depth[c] += 1
        elif c in pair:
            depth[pair[c]] -= 1
            if depth[pair[c]] < 0:
                return False
        i += 1
    return all(v == 0 for v in depth.values()) and quote is None


def main():
    print("\n[1] manifest")
    path = os.path.join(EXT, "manifest.json")
    if not os.path.exists(path):
        check(False, "ext/manifest.json 이 있다")
        return 1
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    check(m.get("manifest_version") == 3, f"MV3 ({m.get('manifest_version')})")
    for key in ("name", "version", "description"):
        check(bool(m.get(key)), f"{key} 가 있다 ({m.get(key)})")
    check(re.match(r"^\d+\.\d+\.\d+$", m.get("version", "")) is not None,
          "버전 모양이 맞다")

    print("\n[2] manifest 가 가리키는 파일이 있는가")
    refs = []
    sw = (m.get("background") or {}).get("service_worker")
    if sw:
        refs.append(sw)
    refs.append((m.get("action") or {}).get("default_popup"))
    for cs in m.get("content_scripts", []):
        refs += list(cs.get("js", [])) + list(cs.get("css", []))
    for r in [x for x in refs if x]:
        check(os.path.exists(os.path.join(EXT, r)), f"{r}")

    print("\n[3] 권한")
    hosts = m.get("host_permissions", [])
    check(any("8900" in h for h in hosts), f"로컬 서버에 닿을 수 있다 ({hosts})")
    # 유튜브 호스트 권한은 tabs.sendMessage 때문에 필요합니다 -- activeTab 은
    # 팝업을 누른 그 순간에만 줍니다. 그 둘 말고는 아무 데도 닿지 않아야
    # 합니다.
    ALLOWED = ("http://localhost:8900/", "http://127.0.0.1:8900/",
               "https://www.youtube.com/")
    stray = [h for h in hosts if not any(h.startswith(a) for a in ALLOWED)]
    check(not stray, f"허락한 곳 밖으로는 열지 않는다 ({stray or '없음'})")
    matches = [x for cs in m.get("content_scripts", []) for x in cs.get("matches", [])]
    check(matches and all("youtube.com" in x for x in matches),
          f"유튜브에서만 돈다 ({matches})")
    # tabCapture 는 3단계에서 씁니다. 지금 없어도 되지만 있으면 적어 둡니다.
    print(f"  ----  권한: {', '.join(m.get('permissions', [])) or '(없음)'}")

    print("\n[4] 자막 모듈 사본이 페이지 쪽과 같은가")
    a = open(os.path.join(WEB, "overlay.js"), "rb").read()
    bpath = os.path.join(EXT, "overlay.js")
    if not os.path.exists(bpath):
        check(False, "ext/overlay.js 가 있다")
    else:
        b = open(bpath, "rb").read()
        check(a == b, "web/overlay.js 와 한 바이트도 다르지 않다"
                      + ("" if a == b else "  →  cp web/overlay.js ext/overlay.js"))

    print("\n[5] 스크립트가 깨지지 않았는가")
    for name in sorted(n for n in os.listdir(EXT) if n.endswith((".js", ".css"))):
        src = open(os.path.join(EXT, name), encoding="utf-8").read()
        check(balanced(src), f"{name} 괄호와 따옴표가 맞다")

    print("\n[6] 탭 소리를 잡는 쪽")
    # 서비스 워커에는 getUserMedia 도 AudioContext 도 없습니다. offscreen
    # 문서가 그 일을 맡는데, 그러려면 권한과 파일이 함께 있어야 합니다.
    if "tabCapture" in m.get("permissions", []):
        check("offscreen" in m.get("permissions", []),
              "tabCapture 를 쓰면 offscreen 권한도 있어야 한다")
        for f in ("offscreen.html", "offscreen.js", "capture-worklet.js"):
            check(os.path.exists(os.path.join(EXT, f)), f"{f} 가 있다")
        off = open(os.path.join(EXT, "offscreen.js"), encoding="utf-8").read()
        check("chromeMediaSource" in off,
              "탭 캡처 제약(chromeMediaSource)을 쓴다")
        check("source.connect(ctx.destination)" in off,
              "잡은 소리를 사용자에게 되돌려 준다 (안 그러면 탭이 음소거됩니다)")
        wl = open(os.path.join(EXT, "capture-worklet.js"), "rb").read()
        webwl = os.path.join(WEB, "capture-worklet.js")
        if os.path.exists(webwl):
            check(wl == open(webwl, "rb").read(),
                  "capture-worklet.js 가 페이지 쪽과 같다"
                  "  →  cp web/capture-worklet.js ext/capture-worklet.js")
        war = [r for w in m.get("web_accessible_resources", [])
               for r in w.get("resources", [])]
        check("capture-worklet.js" in war,
              f"워클릿이 web_accessible_resources 에 있다 ({war})")

    print("\n[7] 서비스 워커가 쓸 수 없는 것을 쓰지 않는가")
    bg = open(os.path.join(EXT, "background.js"), encoding="utf-8").read()
    # MV3 서비스 워커에는 EventSource 도 DOM 도 없습니다. 처음에 EventSource
    # 로 짰다가 아무것도 오지 않았습니다.
    for banned in ("new EventSource", "document.", "window."):
        check(banned not in bg, f"background.js 가 {banned} 를 쓰지 않는다")
    check("getReader()" in bg, "SSE 를 fetch 스트림으로 직접 푼다")

    print("\n[8] 공유 모듈이 확장에서 쓸 수 있는 모양인가")
    ov = open(os.path.join(WEB, "overlay.js"), encoding="utf-8").read()
    check("export " not in ov and "import " not in ov,
          "모듈 문법을 쓰지 않는다 (content script 는 일반 스크립트로 읽습니다)")
    check("MimiOverlay" in ov, "MimiOverlay 를 내놓는다")
    content = open(os.path.join(EXT, "content.js"), encoding="utf-8").read()
    for fn in ("attach", "setData", "setView", "render"):
        check(f".{fn}(" in content or f"MimiOverlay.{fn}" in content,
              f"content.js 가 {fn} 을 쓴다")

    print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}건: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
