"""Check that the browser extension holds its shape.

Whether the extension really runs is something only Chrome can tell you, by
loading it. Even so, some things can be caught before that -- whether the files
the manifest points at actually exist, whether the shared subtitle modules have
drifted away from the page side.

**The subtitle modules are copies.** A content script can only read files inside
the extension folder (MV3 forbids running remote code), so `web/overlay.js` is
kept copied into `ext/`. A copy is bound to drift sooner or later, so it is
guarded here -- two implementations is one story, a mechanical copy is another,
and the latter can be pinned down by a check.

    .venv/bin/python bench/ext_check.py

If they have drifted, line them back up like this:

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

# The files whose source is web/ and whose copy is ext/.
SHARED = ("overlay.js", "cuestore.js", "capture.js", "ytid.js", "capture-worklet.js",
          "i18n.js", "strings-ext.js")


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def balanced(src: str) -> bool:
    """Whether the brackets balance outside quotes and comments. Not a syntax
    check, but eating one half of a pair while editing is caught here."""
    depth = {"{": 0, "(": 0, "[": 0}
    pair = {"}": "{", ")": "(", "]": "["}
    i, n = 0, len(src)
    quote = None
    # The last non-space character seen. This is what tells a `/` that divides
    # from a `/` that opens a regex -- reading the quote in
    # `replace(/[&<>"]/g, …)` as the start of a string throws off everything after it.
    prev = ""
    while i < n:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2; continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "/" and i + 1 < n and src[i + 1] not in "/*" and prev in "(,=:[!&|?{};\n":
            # A regex. Skip to the closing `/`. A `/` inside `[...]` does not close it.
            i += 1
            in_class = False
            while i < n and src[i] != "\n":
                if src[i] == "\\":
                    i += 2; continue
                if src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                elif src[i] == "/" and not in_class:
                    break
                i += 1
            prev = "/"
            i += 1
            continue
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
        if not c.isspace() or c == "\n":
            prev = c
        i += 1
    return all(v == 0 for v in depth.values()) and quote is None


def main():
    print("\n[1] manifest")
    path = os.path.join(EXT, "manifest.json")
    if not os.path.exists(path):
        check(False, "ext/manifest.json exists")
        return 1
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    check(m.get("manifest_version") == 3, f"MV3 ({m.get('manifest_version')})")
    for key in ("name", "version", "description"):
        check(bool(m.get(key)), f"{key} is present ({m.get(key)})")
    check(re.match(r"^\d+\.\d+\.\d+$", m.get("version", "")) is not None,
          "the version has the right shape")

    print("\n[2] do the files the manifest points at exist")
    refs = []
    sw = (m.get("background") or {}).get("service_worker")
    if sw:
        refs.append(sw)
    refs.append((m.get("action") or {}).get("default_popup"))
    for cs in m.get("content_scripts", []):
        refs += list(cs.get("js", [])) + list(cs.get("css", []))
    for r in [x for x in refs if x]:
        check(os.path.exists(os.path.join(EXT, r)), f"{r}")

    print("\n[3] permissions")
    hosts = m.get("host_permissions", [])
    # A pattern that names no port reaches every port. It used to be nailed to
    # 8900, so running the server on another port hit CORS even after changing
    # the popup's "server" field.
    check(any(h in ("http://localhost/*", "http://127.0.0.1/*") for h in hosts),
          f"the local server is reachable on any port ({hosts})")
    # The YouTube host permission is needed for tabs.sendMessage -- activeTab
    # only grants it at the moment the popup is clicked. Apart from those two,
    # it must reach nowhere at all.
    ALLOWED = ("http://localhost/", "http://127.0.0.1/", "https://www.youtube.com/")
    stray = [h for h in hosts if not any(h.startswith(a) for a in ALLOWED)]
    check(not stray, f"nothing is opened outside the allowed hosts ({stray or 'none'})")
    matches = [x for cs in m.get("content_scripts", []) for x in cs.get("matches", [])]
    check(matches and all("youtube.com" in x for x in matches),
          f"it runs on YouTube only ({matches})")
    # tabCapture is used in stage 3. It need not be there yet, but note it if it is.
    print(f"  ----  permissions: {', '.join(m.get('permissions', [])) or '(none)'}")

    print("\n[4] are the shared module copies identical to the page side")
    # What was overlay.js alone has grown to three -- the live cue store
    # (cuestore), tab audio upload (capture), video id (ytid). For all of them
    # web/ is the source and ext/ the copy.
    for name in SHARED:
        a = open(os.path.join(WEB, name), "rb").read()
        bpath = os.path.join(EXT, name)
        if not os.path.exists(bpath):
            check(False, f"ext/{name} exists")
            continue
        b = open(bpath, "rb").read()
        check(a == b, f"not one byte differs from web/{name}"
                      + ("" if a == b else f"  →  cp web/{name} ext/{name}"))
    js_all = [x for cs in m.get("content_scripts", []) for x in cs.get("js", [])]
    for name in ("ytid.js", "cuestore.js"):
        check(name in js_all and js_all.index(name) < js_all.index("content.js"),
              f"{name} is loaded before content.js")
    off_html = open(os.path.join(EXT, "offscreen.html"), encoding="utf-8").read()
    check('src="capture.js"' in off_html and off_html.index("capture.js") < off_html.index("offscreen.js"),
          "offscreen.html loads capture.js before offscreen.js")

    print("\n[5] are the scripts unbroken")
    for name in sorted(n for n in os.listdir(EXT) if n.endswith((".js", ".css"))):
        src = open(os.path.join(EXT, name), encoding="utf-8").read()
        check(balanced(src), f"ext/{name} has balanced brackets and quotes")
    # Look at the page side too. Now that app.js is split across several files,
    # one file with unbalanced brackets kills everything loaded after it.
    for folder in (WEB, os.path.join(WEB, "app")):
        for name in sorted(n for n in os.listdir(folder) if n.endswith(".js")):
            src = open(os.path.join(folder, name), encoding="utf-8").read()
            check(balanced(src), f"{os.path.relpath(os.path.join(folder, name), HERE)} has balanced brackets and quotes")

    print("\n[6] the transcript in the chat slot")
    if os.path.exists(os.path.join(EXT, "panel.js")):
        js = [x for cs in m.get("content_scripts", []) for x in cs.get("js", [])]
        check("panel.js" in js, f"panel.js is in content_scripts ({js})")
        check(js.index("panel.js") < js.index("content.js"),
              "it is loaded before content.js (which uses MimiPanel)")
        pan = open(os.path.join(EXT, "panel.js"), encoding="utf-8").read()
        check("MimiPanel" in pan, "it exposes MimiPanel")
        check("#secondary" in pan, "it finds YouTube's right-hand column")
        check("hidden.style.display" in pan or 'hidden.style.display = ""' in pan,
              "it puts the hidden chat back")
        css = open(os.path.join(EXT, "overlay.css"), encoding="utf-8").read()
        check(".mw-panel" in css, "the transcript panel styling is there")

    print("\n[7-1] the tab-audio capture side")
    # A service worker has neither getUserMedia nor AudioContext. The offscreen
    # document takes that job, and for that the permission and the files have
    # to be there together.
    if "tabCapture" in m.get("permissions", []):
        check("offscreen" in m.get("permissions", []),
              "using tabCapture demands the offscreen permission too")
        for f in ("offscreen.html", "offscreen.js", "capture-worklet.js"):
            check(os.path.exists(os.path.join(EXT, f)), f"{f} exists")
        off = open(os.path.join(EXT, "offscreen.js"), encoding="utf-8").read()
        check("chromeMediaSource" in off,
              "it uses the tab-capture constraint (chromeMediaSource)")
        # It does have to be played back. Capturing with tabCapture makes that
        # tab's sound inaudible to the user.
        check("srcObject = media" in off and ".play()" in off,
              "the captured sound is played back (otherwise the tab goes silent)")
        # But **it must not go through our graph.** That context is 16kHz, so
        # routing it there shaves 48kHz stereo down to telephone quality.
        check("source.connect(ctx.destination)" not in off,
              "the audible sound is not routed into the 16kHz context")
        # The graph itself lives in capture.js, shared with the page.
        capjs = open(os.path.join(EXT, "capture.js"), encoding="utf-8").read()
        check("sampleRate: 16000" in capjs, "only the transcription side is 16kHz (capture.js)")
        check("channelCount: 1" in capjs,
              "Web Audio does the stereo fold-down (not just the left channel)")
        check("MimiCapture.start" in off, "offscreen.js captures through the shared module")
        wl = open(os.path.join(EXT, "capture-worklet.js"), "rb").read()
        webwl = os.path.join(WEB, "capture-worklet.js")
        if os.path.exists(webwl):
            check(wl == open(webwl, "rb").read(),
                  "capture-worklet.js matches the page side"
                  "  →  cp web/capture-worklet.js ext/capture-worklet.js")
        war = [r for w in m.get("web_accessible_resources", [])
               for r in w.get("resources", [])]
        check("capture-worklet.js" in war,
              f"the worklet is in web_accessible_resources ({war})")

    print("\n[6-1] is it immune to the host page's font size")
    # YouTube sets `html` to 10px (normally 16px). Using rem renders our UI at
    # 62.5% size -- a subtitle meant to be 12.8px came out at 8px.
    css = open(os.path.join(EXT, "overlay.css"), encoding="utf-8").read()
    rems = re.findall(r"[\d.]+rem", css)
    check(not rems, f"no rem in the extension CSS ({rems or 'none'})")

    print("\n[7] does it avoid what the YouTube document forbids")
    # YouTube has Trusted Types turned on (`require-trusted-types-for
    # 'script'`). Assigning a string to innerHTML in that document is refused --
    # confirmed on the YouTube page itself. Whether a content script is exempt
    # varies by Chrome version, so we do not lean on it at all.
    for name in ("content.js", "panel.js"):
        src = open(os.path.join(EXT, name), encoding="utf-8").read()
        used = re.findall(r"^\s*[^/*\n]*\.innerHTML\s*=", src, re.M)
        check(not used, f"{name} does not write through innerHTML ({len(used)} places)")

    print("\n[8] does it avoid what a service worker cannot use")
    bg = open(os.path.join(EXT, "background.js"), encoding="utf-8").read()
    # An MV3 service worker has neither EventSource nor a DOM. The first cut
    # was written with EventSource and nothing ever arrived.
    for banned in ("new EventSource", "document.", "window."):
        check(banned not in bg, f"background.js does not use {banned}")
    check("getReader()" in bg, "it parses SSE from a fetch stream by hand")

    print("\n[9] are the shared modules in a shape the extension can use")
    ov = open(os.path.join(WEB, "overlay.js"), encoding="utf-8").read()
    check("export " not in ov and "import " not in ov,
          "no module syntax (a content script is loaded as a plain script)")
    check("MimiOverlay" in ov, "it exposes MimiOverlay")
    content = open(os.path.join(EXT, "content.js"), encoding="utf-8").read()
    for fn in ("attach", "setData", "setView", "render"):
        check(f".{fn}(" in content or f"MimiOverlay.{fn}" in content,
              f"content.js uses {fn}")

    print("\n" + ("all passed" if not FAIL else f"{len(FAIL)} failed: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
