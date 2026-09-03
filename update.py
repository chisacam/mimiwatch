"""Checks GitHub releases for a new version and, as a bundle (PyInstaller),
downloads it and swaps it in.

All three stages are here.

  Check    -- Asks releases/latest once a day (or from the button on the screen)
              and compares the tag with the version running now. This one query
              is the only request that goes out. It can be turned off with
              `"update_check": false` in `backends.json` or the environment
              variable MIMIWATCH_NO_UPDATE_CHECK.
  Download -- Fetches this platform's asset (zip/tar.gz) into updates/ in the
              user area. It goes into a `.part` and resumes, by the same rule as
              a model download.
  Apply    -- An executable cannot overwrite itself, so a swap script is
              launched and the server shuts itself down. The script waits for
              the process to end, moves the old bundle aside as `.old`, puts the
              new one in its place, and launches it again with the same
              arguments. If the swap fails, the old one is put back.

**Running from the repository it only notifies.** There is no bundle to swap in,
so apply is refused, and the screen points at `git pull`.

Where the current version comes from: a bundle carries a `_version.txt` baked in
by the build (packaging/mimiwatch.spec), and from the repository it is
`git describe`. When the tag cannot be read (only a hash), there is no ground
for a comparison, so we do not say there is a new version.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import bus
import paths

REPO = "chisacam/mimiwatch"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
USER_AGENT = "mimiwatch (+https://github.com/chisacam/mimiwatch)"
CHUNK = 1 << 20
PROGRESS_EVERY_S = 0.4
# The minimum interval between automatic checks. The screen's "Check for a new
# version" (force) ignores it.
CHECK_EVERY_S = 24 * 3600.0

_lock = threading.Lock()
# state: idle | checking | downloading | ready | applying | error
_state: dict = {"state": "idle", "error": None, "progress": None,
                "latest": None, "available": False, "checked": 0.0, "file": None}
_worker: threading.Thread | None = None
_auto: threading.Thread | None = None


# ---- Version numbers ----------------------------------------------------------

def current_version() -> str:
    """The version running now. The bundle's `_version.txt` → environment variable
    → git describe → 0.0.0."""
    try:
        with open(os.path.join(paths.BASE, "_version.txt"), encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            return v
    except OSError:
        pass
    v = (os.environ.get("MIMIWATCH_VERSION") or "").strip()
    if v:
        return v
    try:
        out = subprocess.run(["git", "-C", paths.BASE, "describe", "--tags", "--always"],
                             capture_output=True, text=True, timeout=5)
        v = out.stdout.strip()
    except Exception:
        v = ""
    return v or "0.0.0"


def parse_version(v: str) -> tuple[int, ...]:
    """Only the numeric part of `v0.3.2` or `0.3.2-5-gabc`. An empty tuple when it
    cannot be read."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(latest: str, current: str) -> bool:
    """Is the release tag newer than the version running now?

    Both sides have to be readable for this to be true at all. If the current
    version is only a tagless hash, that means there is no ground for a
    comparison, not that there is a new version -- no banner should stand up
    during development. A working copy ahead of the tag (`0.3.2-5-g…`) is
    measured as 0.3.2 too, so a release of that same tag is not called a new
    version.
    """
    pl, pc = parse_version(latest), parse_version(current)
    return bool(pl) and bool(pc) and pl > pc


def pick_asset(assets: list[dict], platform: str | None = None,
               machine: str | None = None) -> dict | None:
    """This platform's bundle asset. The names are exactly as the build script makes
    them.

    The Windows `-cuda`/`-cpu` variants are not picked -- the release default is
    Vulkan alone, and anyone using a variant built it themselves, so it is not
    subject to automatic updates.
    """
    plat = platform or sys.platform
    mach = machine or __import__("platform").machine().lower()
    if plat == "darwin":
        arch = "arm64" if mach == "arm64" else "x64"
        pat = re.compile(rf"^mimiwatch-.*-macos-{arch}\.zip$")
    elif plat == "win32":
        pat = re.compile(r"^mimiwatch-.*-windows-x64\.zip$")
    else:
        pat = re.compile(rf"^mimiwatch-.*-linux-{re.escape(mach)}\.tar\.gz$")
    for a in assets:
        if pat.match(a.get("name") or ""):
            return {"name": a["name"], "url": a.get("browser_download_url") or "",
                    "size": int(a.get("size") or 0)}
    return None


# ---- Check ---------------------------------------------------------------------

def _dir() -> str:
    d = os.path.join(paths.home(), "updates")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path() -> str:
    return os.path.join(_dir(), "check.json")


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def status() -> dict:
    """The state the screen sees. It does not go out to the network."""
    with _lock:
        st = dict(_state)
    st.pop("file", None)
    st["current"] = current_version()
    st["frozen"] = paths.frozen()
    return st


def check(force: bool = False) -> dict:
    """Asks releases/latest and fixes up the state. The interval between checks is a
    day -- the last result is left in a file so that a restart does not ask again."""
    with _lock:
        checked = _state["checked"]
        busy = _state["state"] in ("downloading", "applying")
    if not force and not checked:
        # Just after a restart: if the last result is still within the day, use it.
        try:
            with open(_cache_path(), encoding="utf-8") as f:
                saved = json.load(f)
            if time.time() - float(saved.get("checked") or 0) < CHECK_EVERY_S:
                with _lock:
                    _state.update(checked=saved["checked"], latest=saved.get("latest"),
                                  available=_still_available(saved.get("latest")))
                return status()
        except Exception:
            pass
    if not force and checked and time.time() - checked < CHECK_EVERY_S:
        return status()
    if busy:
        return status()          # We do not swap the listing while downloading
    try:
        rel = _get_json(API_LATEST)
        tag = (rel.get("tag_name") or "").strip()
        latest = {"tag": tag,
                  "url": rel.get("html_url") or f"https://github.com/{REPO}/releases",
                  "notes": (rel.get("body") or "")[:2000],
                  "asset": pick_asset(rel.get("assets") or [])}
        with _lock:
            was = _state["available"]
            _state.update(checked=time.time(), latest=latest,
                          available=is_newer(tag, current_version()), error=None)
            if _state["state"] == "error":
                _state["state"] = "idle"
            fresh = _state["available"] and not was
        try:
            with open(_cache_path(), "w", encoding="utf-8") as f:
                json.dump({"checked": time.time(), "latest": latest}, f)
        except OSError:
            pass
        if fresh:
            _publish()           # Stands the banner up on any open screen
    except Exception as exc:
        with _lock:
            _state.update(checked=time.time(), error=f"확인 실패: {str(exc)[:200]}")
    return status()


def _still_available(latest: dict | None) -> bool:
    return bool(latest) and is_newer(latest.get("tag") or "", current_version())


def _publish():
    bus.publish({"type": "update", **status()})


# ---- Download ------------------------------------------------------------------

def download() -> dict:
    with _lock:
        if _state["state"] in ("downloading", "applying"):
            pass
        elif not _state["available"] or not (_state["latest"] or {}).get("asset"):
            return {"error": "받을 새 판이 없습니다. 먼저 확인하십시오."}
        else:
            _state.update(state="downloading", error=None, progress={"done": 0, "total": 0})
            asset = _state["latest"]["asset"]
            global _worker
            _worker = threading.Thread(target=_download, args=(dict(asset),), daemon=True)
            _worker.start()
    return status()


def _download(asset: dict):
    dest = os.path.join(_dir(), os.path.basename(asset["name"]))
    part = dest + ".part"
    try:
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"User-Agent": USER_AGENT}
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(asset["url"], headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            if have and resp.status != 206:
                have = 0
            length = resp.headers.get("Content-Length")
            total = (int(length) + have) if length else int(asset.get("size") or 0)
            done, last = have, 0.0
            with open(part, "ab" if have else "wb") as f:
                while True:
                    buf = resp.read(CHUNK)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    now = time.time()
                    if now - last >= PROGRESS_EVERY_S:
                        last = now
                        with _lock:
                            _state["progress"] = {"done": done, "total": total}
                        _publish()
        if total and done < total:
            raise RuntimeError(f"받다 끊겼습니다 ({done}/{total} 바이트). "
                               "다시 누르면 이어 받습니다.")
        os.replace(part, dest)
        with _lock:
            _state.update(state="ready", file=dest,
                          progress={"done": done, "total": total or done})
    except Exception as exc:
        with _lock:
            _state.update(state="error", error=str(exc)[:300])
    _publish()


# ---- Apply ---------------------------------------------------------------------

def apply() -> dict:
    """Launches the swap script. The caller (server) sends the response and shuts down."""
    if not paths.frozen():
        return {"error": "저장소에서 돌고 있습니다. `git pull` 로 받으십시오 -- "
                         "갈아 끼울 묶음이 없습니다."}
    with _lock:
        file = _state.get("file")
        if _state["state"] != "ready" or not file or not os.path.isfile(file):
            return {"error": "먼저 새 판을 받아야 합니다."}
        _state["state"] = "applying"
    try:
        script = _stage_and_script(file)
    except Exception as exc:
        with _lock:
            _state.update(state="error", error=str(exc)[:300])
        return {"error": str(exc)[:300]}
    log = open(os.path.join(_dir(), "apply.log"), "ab")
    if sys.platform == "win32":
        DETACHED = 0x00000008 | 0x00000200      # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", script],
                         creationflags=DETACHED, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log)
    else:
        subprocess.Popen(["/bin/bash", script], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    return {"ok": True, "restarting": True}


def _stage_and_script(zip_path: str) -> str:
    """Extracts the new version into stage/ and writes the script that will do the
    swap once the process has ended.

    On macOS the extraction is `ditto` -- Python's zipfile loses the symlinks
    and the executable bits inside the .app, and the extracted app then does not
    open.
    """
    stage = os.path.join(_dir(), "stage")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    exe = sys.executable
    args = sys.argv[1:]

    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", zip_path, stage], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        new_app = os.path.join(stage, "mimiwatch.app")
        if not os.path.isdir(new_app):
            raise RuntimeError("받은 zip 안에 mimiwatch.app 이 없습니다")
        app = os.path.dirname(os.path.dirname(os.path.dirname(exe)))   # .../mimiwatch.app
        if os.path.basename(app) != "mimiwatch.app":
            raise RuntimeError(f".app 묶음이 아닙니다: {exe}")
        parent = os.path.dirname(app)
        if not os.access(parent, os.W_OK):
            raise RuntimeError(f"{parent} 에 쓸 수 없어 갈아 끼우지 못합니다. "
                               "앱을 직접 바꿔 넣으십시오.")
        old = app + ".old"
        open_args = " --args " + shlex.join(args) if args else ""
        body = f"""#!/bin/bash
# mimiwatch 판올림 교체 스크립트. apply.log 로 남습니다.
echo "== apply $(date) pid={os.getpid()}"
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done
sleep 0.5
rm -rf {shlex.quote(old)}
if mv {shlex.quote(app)} {shlex.quote(old)} && mv {shlex.quote(new_app)} {shlex.quote(app)}; then
  xattr -cr {shlex.quote(app)} 2>/dev/null || true
  echo "swapped"
else
  echo "swap failed; restoring old app"
  [ -e {shlex.quote(app)} ] || mv {shlex.quote(old)} {shlex.quote(app)}
fi
open -n {shlex.quote(app)}{open_args}
"""
    elif sys.platform == "win32":
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(stage)
        new_dir = os.path.join(stage, "mimiwatch")
        if not os.path.isdir(new_dir):
            raise RuntimeError("받은 zip 안에 mimiwatch 폴더가 없습니다")
        root = os.path.dirname(exe)                                    # ...\mimiwatch
        old = root + ".old"
        q = lambda p: "'" + p.replace("'", "''") + "'"
        arg_list = (", ".join(q(a) for a in args)) or ""
        start = (f"Start-Process -FilePath {q(os.path.join(root, 'mimiwatch.exe'))}"
                 + (f" -ArgumentList {arg_list}" if arg_list else ""))
        body = f"""# mimiwatch 판올림 교체 스크립트. apply.log 로 남습니다.
Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 700
Remove-Item -Recurse -Force {q(old)} -ErrorAction SilentlyContinue
try {{
  Move-Item -Force {q(root)} {q(old)} -ErrorAction Stop
  Move-Item -Force {q(new_dir)} {q(root)} -ErrorAction Stop
  "swapped"
}} catch {{
  "swap failed: $_"
  if (-not (Test-Path {q(root)})) {{ Move-Item -Force {q(old)} {q(root)} }}
}}
{start}
"""
    else:
        subprocess.run(["tar", "-xzf", zip_path, "-C", stage], check=True)
        new_dir = os.path.join(stage, "mimiwatch")
        if not os.path.isdir(new_dir):
            raise RuntimeError("받은 것 안에 mimiwatch 폴더가 없습니다")
        root = os.path.dirname(exe)
        old = root + ".old"
        run_args = " " + shlex.join(args) if args else ""
        body = f"""#!/bin/bash
echo "== apply $(date) pid={os.getpid()}"
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done
sleep 0.5
rm -rf {shlex.quote(old)}
if mv {shlex.quote(root)} {shlex.quote(old)} && mv {shlex.quote(new_dir)} {shlex.quote(root)}; then
  echo "swapped"
else
  echo "swap failed; restoring"
  [ -e {shlex.quote(root)} ] || mv {shlex.quote(old)} {shlex.quote(root)}
fi
nohup {shlex.quote(os.path.join(root, "mimiwatch"))}{run_args} >/dev/null 2>&1 &
"""
    ext = ".ps1" if sys.platform == "win32" else ".sh"
    script = os.path.join(_dir(), "apply" + ext)
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    if sys.platform != "win32":
        os.chmod(script, 0o755)
    return script


# ---- Automatic check -----------------------------------------------------------

def _enabled() -> bool:
    if os.environ.get("MIMIWATCH_NO_UPDATE_CHECK"):
        return False
    try:
        import config
        return bool(config.load().get("update_check", True))
    except Exception:
        return True


def start_auto_check():
    """Checks once a day in the background. It is put off a little so as not to slow
    the startup itself."""
    global _auto
    if _auto is not None or not _enabled():
        return

    def loop():
        time.sleep(10)
        while True:
            try:
                check()
            except Exception:
                pass
            time.sleep(CHECK_EVERY_S)

    _auto = threading.Thread(target=loop, daemon=True)
    _auto.start()
