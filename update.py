"""깃허브 릴리스로 새 판을 확인하고, 묶음(PyInstaller)이면 받아서 갈아 끼웁니다.

세 단계가 전부 여기에 있습니다.

  확인   -- releases/latest 를 하루 한 번(또는 화면의 단추로) 물어 태그를
            지금 판과 비교합니다. 밖으로 나가는 요청은 이 조회 하나입니다.
            `backends.json` 의 `"update_check": false` 나 환경변수
            MIMIWATCH_NO_UPDATE_CHECK 로 끌 수 있습니다.
  내려받기 -- 이 플랫폼의 자산(zip/tar.gz)을 사용자 영역의 updates/ 에 받습니다.
            모델 내려받기와 같은 규칙으로 `.part` 에 받고 이어 받습니다.
  적용   -- 실행 파일은 자기 자신을 덮어쓸 수 없으므로, 교체 스크립트를
            띄워 두고 서버가 스스로 꺼집니다. 스크립트는 프로세스가 끝나기를
            기다렸다가 옛 묶음을 `.old` 로 물리고 새 것을 그 자리에 놓은 뒤
            같은 인자로 다시 띄웁니다. 교체가 실패하면 옛것을 되돌립니다.

**저장소에서 돌 때는 알리기만 합니다.** 갈아 끼울 묶음이 없으니 적용은
거절하고, 화면은 `git pull` 을 안내합니다.

지금 판이 어디서 오는가: 묶음에는 빌드가 구운 `_version.txt` 가 들어 있고
(packaging/mimiwatch.spec), 저장소에서는 `git describe` 입니다. 태그를 못
읽으면(해시뿐이면) 비교할 근거가 없으므로 새 판이 있다고 말하지 않습니다.
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
# 자동 확인 사이의 최소 간격. 화면의 「새 판 확인」(force)은 이것을 무시합니다.
CHECK_EVERY_S = 24 * 3600.0

_lock = threading.Lock()
# state: idle | checking | downloading | ready | applying | error
_state: dict = {"state": "idle", "error": None, "progress": None,
                "latest": None, "available": False, "checked": 0.0, "file": None}
_worker: threading.Thread | None = None
_auto: threading.Thread | None = None


# ---- 판 번호 ------------------------------------------------------------------

def current_version() -> str:
    """지금 도는 판. 묶음의 `_version.txt` → 환경변수 → git describe → 0.0.0."""
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
    """`v0.3.2` 나 `0.3.2-5-gabc` 에서 숫자 부분만. 못 읽으면 빈 튜플."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(latest: str, current: str) -> bool:
    """릴리스 태그가 지금 판보다 새로운가.

    양쪽 다 읽혀야 참일 수 있습니다. 지금 판이 태그 없는 해시뿐이라면 비교할
    근거가 없는 것이지 새 판이 있는 것이 아닙니다 -- 개발 중에 띠가 서지
    않아야 합니다. 태그보다 앞선 작업본(`0.3.2-5-g…`)도 기준은 0.3.2 이므로
    같은 태그의 릴리스를 새 판이라고 말하지 않습니다.
    """
    pl, pc = parse_version(latest), parse_version(current)
    return bool(pl) and bool(pc) and pl > pc


def pick_asset(assets: list[dict], platform: str | None = None,
               machine: str | None = None) -> dict | None:
    """이 플랫폼의 묶음 자산. 이름은 빌드 스크립트가 짓는 그대로입니다.

    윈도우의 `-cuda`/`-cpu` 변형은 고르지 않습니다 -- 릴리스의 기본은 Vulkan
    하나이고, 변형을 쓰는 사람은 직접 만든 것이라 자동 판올림 대상이 아닙니다.
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


# ---- 확인 ----------------------------------------------------------------------

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
    """화면이 보는 상태. 네트워크에 나가지 않습니다."""
    with _lock:
        st = dict(_state)
    st.pop("file", None)
    st["current"] = current_version()
    st["frozen"] = paths.frozen()
    return st


def check(force: bool = False) -> dict:
    """releases/latest 를 물어 상태를 고칩니다. 확인 사이 간격은 하루입니다 --
    재시작해도 다시 묻지 않게 마지막 결과를 파일로 남깁니다."""
    with _lock:
        checked = _state["checked"]
        busy = _state["state"] in ("downloading", "applying")
    if not force and not checked:
        # 재시작 직후: 지난 결과가 아직 하루 안이면 그것을 씁니다.
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
        return status()          # 받는 중에 목록을 갈아 끼우지 않습니다
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
            _publish()           # 열려 있는 화면에 띠를 세웁니다
    except Exception as exc:
        with _lock:
            _state.update(checked=time.time(), error=f"확인 실패: {str(exc)[:200]}")
    return status()


def _still_available(latest: dict | None) -> bool:
    return bool(latest) and is_newer(latest.get("tag") or "", current_version())


def _publish():
    bus.publish({"type": "update", **status()})


# ---- 내려받기 -------------------------------------------------------------------

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


# ---- 적용 ----------------------------------------------------------------------

def apply() -> dict:
    """교체 스크립트를 띄웁니다. 부르는 쪽(server)이 응답을 보내고 꺼집니다."""
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
    """새 판을 stage/ 에 풀고, 프로세스가 끝난 뒤 바꿔치기할 스크립트를 씁니다.

    맥의 압축 풀기는 `ditto` 입니다 -- 파이썬 zipfile 은 .app 안의 심볼릭 링크와
    실행 권한을 잃어버려, 풀어 놓은 앱이 열리지 않습니다.
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


# ---- 자동 확인 ------------------------------------------------------------------

def _enabled() -> bool:
    if os.environ.get("MIMIWATCH_NO_UPDATE_CHECK"):
        return False
    try:
        import config
        return bool(config.load().get("update_check", True))
    except Exception:
        return True


def start_auto_check():
    """하루 한 번 배경에서 확인합니다. 기동 자체를 늦추지 않게 조금 미룹니다."""
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
