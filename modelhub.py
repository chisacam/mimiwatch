"""모델과 실행 도구(ffmpeg, yt-dlp)를 내려받고 관리하는 한 곳.

예전에는 설치 스크립트 둘(install.sh, install.ps1)이 저마다 모델 주소 목록을
들고 있었고, 화면에서는 무엇이 있고 없는지 볼 길이 없었습니다. 모델 파일이
없으면 세션이 `FileNotFoundError`로 끝나고 "./install.sh 를 실행하십시오"라는
말만 남았습니다. 묶음(PyInstaller)으로 배포하면 그 스크립트조차 없습니다.

여기서 하는 일은 셋입니다.

  - **목록(CATALOG)**: 무엇을 어디서 받는지. 설치 스크립트와 화면이 같은 표를 봅니다.
  - **상태**: 있음 · 없음 · 받다 만 것(`.part`) · 받는 중 · 실패. 화면의 「모델·도구」가
    그대로 보여 주고, 진행은 `bus`로 밀어 줍니다.
  - **내려받기**: 작업 스레드 하나가 줄을 선 순서대로 받습니다. 5GB짜리 둘을 나란히
    받아 봐야 빨라지지 않고 진행 표시만 어지럽습니다. `.part`에 받고 다 받은 뒤
    이름을 바꿉니다 -- 끊긴 것이 완성본으로 보이지 않고, `Range`로 이어 받습니다.

모델은 늘 `stream.model_dir()`(= `paths.model_dir()`)에, 도구는 `paths.tools_dir()`에
둡니다. 저장소 밖입니다. 묶음을 새 판으로 바꾸거나 저장소를 지워도 6GB를 다시
받지 않습니다.

**허깅페이스에서 직접 받기.** 기본 목록 밖의 GGUF를 쓰려면 저장소 id와 파일 이름을
적습니다(`add_custom`). 받은 뒤에는 `backends.json`에 엔진 항목을 넣어 화면의
선택기에 나타나게 합니다. 전사는 transcribe.cpp가 읽는 GGUF여야 하고, 번역은
llama.cpp가 읽는 채팅 모델이어야 합니다 -- 그것은 여기서 검사할 수 없으므로
받은 뒤 처음 쓸 때 드러납니다.

`huggingface_hub`는 쓰지 않습니다. 필요한 것은 `resolve/main/<파일>` 주소 하나와,
여러 파일로 된 모델의 파일 목록(`api/models/<repo>`)뿐이라 표준 라이브러리로
충분하고, 묶음이 그만큼 가벼워집니다.
"""
from __future__ import annotations

import gzip
import json
import os
import platform
import queue
import shutil
import stat
import sys
import threading
import time
import urllib.error
import urllib.request

import bus
import config
import paths

HF = "https://huggingface.co"
GH_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
# 정적으로 빌드된 ffmpeg 하나짜리 실행 파일. 맥(arm64·x64)·윈도우·리눅스가 한 곳에
# 있고 판이 태그로 고정되어 있어 주소가 흔들리지 않습니다. GPL 빌드이지만 우리가
# 배포하는 것이 아니라 사용자가 받는 것입니다 -- 묶음에 넣지 않는 이유입니다.
FFMPEG_STATIC = "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
YTDLP_LATEST = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"

USER_AGENT = "mimiwatch/1.0 (+https://github.com/chisacam/mimiwatch)"
CHUNK = 1 << 20
# 진행 알림은 이 간격으로만 냅니다. 초당 100MB를 받으면 1MB마다 알리는 것은 소음입니다.
PROGRESS_EVERY_S = 0.4

# 크기는 표시용 어림값(바이트)입니다. 실제 크기는 받을 때 Content-Length로 압니다.
CATALOG: list[dict] = [
    {"id": "silero-vad", "kind": "aux", "label": "Silero VAD",
     "purpose": "발화 구간 분할 · 전사에 반드시 필요", "file": "silero_vad.onnx",
     "url": f"{GH_SHERPA}/asr-models/silero_vad.onnx", "size": 643_854,
     "required": True, "default": True},
    {"id": "whisper-large-v3-turbo", "kind": "asr", "label": "Whisper large-v3-turbo Q8_0",
     "purpose": "전사 (모든 언어) · 품질 · GPU 권장", "file": "whisper-large-v3-turbo-Q8_0.gguf",
     "repo": "handy-computer/whisper-large-v3-turbo-gguf", "size": 886_381_760,
     "required": True, "default": True},
    {"id": "sensevoice-small", "kind": "asr", "label": "SenseVoice Small Q8_0",
     "purpose": "전사 · 기본 (가벼움 · CPU, whisper의 8배 빠름)",
     "file": "SenseVoiceSmall-Q8_0.gguf",
     "repo": "handy-computer/SenseVoiceSmall-gguf", "size": 252_684_608,
     "required": False, "default": True},
    {"id": "moonshine-base", "kind": "asr", "label": "Moonshine base Q8_0",
     "purpose": "가벼운 전사 · 영어 전용 (74MB, 품질은 whisper와 같음)",
     "file": "moonshine-base-Q8_0.gguf",
     "repo": "handy-computer/moonshine-base-gguf", "size": 77_476_480,
     "required": False, "default": True},
    {"id": "gemma-4-e4b", "kind": "tr", "label": "Gemma 4 E4B q4_0",
     "purpose": "번역 · 품질 · GPU 권장 (4.9GB, M2M-100과 품질 차이가 큼)",
     "file": "gemma-4-E4B_q4_0-it.gguf",
     "repo": "google/gemma-4-E4B-it-qat-q4_0-gguf", "size": 5_154_941_280,
     "required": False, "default": True},
    {"id": "m2m100", "kind": "tr", "label": "M2M-100 (CTranslate2)",
     "purpose": "번역 · 기본 (가벼움 · CPU) · Gemma의 대체용도 겸함", "dir": "mojicast-m2m100-ct2",
     "repo": "ishiki-emo/mojicast-m2m100-ct2", "size": 473_000_000,
     "required": True, "default": True},
    {"id": "campplus", "kind": "aux", "label": "CAM++",
     "purpose": "녹화본 화자 태그 (선택)", "file": "campplus_sv.onnx",
     "url": f"{GH_SHERPA}/speaker-recongition-models/"
            "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
     "size": 28_281_164, "required": False, "default": True},
    # 도구. 시스템에 있으면 받을 필요가 없습니다 -- 상태에 그렇게 적습니다.
    {"id": "ffmpeg", "kind": "tool", "label": "ffmpeg",
     "purpose": "오디오 풀기 · 시스템에 없을 때만 필요", "tool": "ffmpeg",
     "size": 45_000_000, "required": True, "default": False},
    {"id": "yt-dlp", "kind": "tool", "label": "yt-dlp (독립 실행 파일)",
     "purpose": "영상 주소 해석 · 스스로 판올림하는 판. 묶음으로 쓸 때 권장",
     "tool": "yt-dlp", "size": 37_000_000, "required": False, "default": False},
]

# 사용자가 허깅페이스에서 직접 추가한 모델. 모델 디렉터리 안에 함께 둡니다 --
# 모델과 같은 수명이어야 하니까요.
CUSTOM_FILE = "custom.json"

_lock = threading.Lock()
_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_progress: dict[str, dict] = {}        # 받는 중 · 실패한 것의 진행/오류
_cancel: set[str] = set()
_queued: list[str] = []


# ---- 목록 --------------------------------------------------------------------

def _custom_path() -> str:
    return os.path.join(paths.model_dir(), CUSTOM_FILE)


def custom_entries() -> list[dict]:
    try:
        with open(_custom_path(), encoding="utf-8") as f:
            got = json.load(f)
        return [dict(e, custom=True) for e in got if isinstance(e, dict) and e.get("id")]
    except Exception:
        return []


def _save_custom(entries: list[dict]):
    os.makedirs(paths.model_dir(), exist_ok=True)
    tmp = _custom_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in e.items() if k != "custom"} for e in entries],
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, _custom_path())


def entries() -> list[dict]:
    return [dict(e) for e in CATALOG] + custom_entries()


def find(model_id: str) -> dict | None:
    for e in entries():
        if e["id"] == model_id:
            return e
    return None


def _tool_asset(name: str) -> tuple[str, bool]:
    """(주소, gzip 여부). 플랫폼에 맞는 실행 파일 하나."""
    arch = platform.machine().lower()
    if name == "ffmpeg":
        if sys.platform == "darwin":
            mac_arch = "arm64" if arch in ("arm64", "aarch64") else "x64"
            return f"{FFMPEG_STATIC}/ffmpeg-darwin-{mac_arch}.gz", True
        if sys.platform == "win32":
            return f"{FFMPEG_STATIC}/ffmpeg-win32-x64.gz", True
        return f"{FFMPEG_STATIC}/ffmpeg-linux-x64.gz", True
    if name == "yt-dlp":
        if sys.platform == "darwin":
            return f"{YTDLP_LATEST}/yt-dlp_macos", False
        if sys.platform == "win32":
            return f"{YTDLP_LATEST}/yt-dlp.exe", False
        return f"{YTDLP_LATEST}/yt-dlp_linux", False
    raise ValueError(name)


def target_path(entry: dict) -> str:
    """이 항목이 완성되면 놓이는 자리."""
    if entry.get("tool"):
        return os.path.join(paths.tools_dir(),
                            entry["tool"] + (".exe" if sys.platform == "win32" else ""))
    if entry.get("dir"):
        return os.path.join(paths.model_dir(), entry["dir"])
    return os.path.join(paths.model_dir(), entry["file"])


def _dir_ready(path: str) -> bool:
    return (os.path.isdir(path) and not os.path.exists(os.path.join(path, ".incomplete"))
            and any(os.scandir(path)))


def _dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def status(entry: dict) -> dict:
    """항목 하나의 지금 상태."""
    path = target_path(entry)
    out = {k: entry.get(k) for k in ("id", "kind", "label", "purpose", "size", "required",
                                      "default", "repo", "file", "dir", "tool", "custom")}
    out["path"] = path
    with _lock:
        prog = dict(_progress.get(entry["id"]) or {})
        queued = entry["id"] in _queued
    if prog.get("state") == "downloading":
        out.update(prog)
        return out
    if queued:
        out.update({"state": "queued", "done": 0, "total": entry.get("size") or 0})
        return out
    if entry.get("dir"):
        ready = _dir_ready(path)
        out["have"] = _dir_size(path) if os.path.isdir(path) else 0
        out["state"] = "ready" if ready else ("partial" if os.path.isdir(path) else "missing")
    else:
        if os.path.isfile(path):
            out["have"] = os.path.getsize(path)
            out["state"] = "ready"
        elif os.path.isfile(path + ".part"):
            out["have"] = os.path.getsize(path + ".part")
            out["state"] = "partial"
        else:
            out["have"] = 0
            out["state"] = "missing"
    if entry.get("tool"):
        # 시스템에 있으면 받지 않아도 됩니다. 어느 것을 실제로 쓰는지도 적습니다.
        system = shutil.which(entry["tool"]) or next(
            (os.path.join(d, entry["tool"]) for d in paths._EXTRA_PATH
             if os.path.isfile(os.path.join(d, entry["tool"]))), None)
        out["system"] = system
        if out["state"] != "ready" and system:
            out["state"] = "system"
    if prog.get("state") == "error" and out["state"] != "ready":
        # 실패는 다시 받거나 지울 때까지 남습니다. 받다 만 조각이 있어도 「실패」로
        # 보여야 왜 멎었는지가 눈에 들어옵니다 -- 「다시」를 누르면 이어 받습니다.
        out["state"] = "error"
        out["error"] = prog.get("error")
    return out


def other_files() -> list[dict]:
    """모델 디렉터리에 있는데 목록에 없는 파일. 손으로 넣은 GGUF 같은 것들 --
    지울 수 있어야 하고, 전사 엔진 설정에 이름을 적어 쓸 수도 있습니다."""
    known = set()
    for e in entries():
        known.add(os.path.basename(target_path(e)))
        if not e.get("tool"):
            known.add(os.path.basename(target_path(e)) + ".part")
    known.add(CUSTOM_FILE)
    out = []
    mdir = paths.model_dir()
    if not os.path.isdir(mdir):
        return out
    for name in sorted(os.listdir(mdir)):
        if name in known or name.startswith("."):
            continue
        p = os.path.join(mdir, name)
        size = _dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
        out.append({"id": "file:" + name, "kind": "other", "label": name,
                    "purpose": "목록에 없는 파일", "path": p, "have": size, "size": size,
                    "state": "ready", "file": name})
    return out


def _entry_for_file(name: str) -> dict | None:
    """파일 이름(또는 디렉터리 이름)으로 목록 항목을 찾습니다."""
    base = os.path.basename(name)
    for e in entries():
        if e.get("file") == base or e.get("dir") == base:
            return e
    return None


def required_ids(cfg: dict | None = None) -> list[str]:
    """지금 설정으로 돌리는 데 **반드시** 있어야 하는 것.

    고정된 표가 아닙니다. 기본 전사기가 SenseVoice 면 whisper 는 없어도 되고, 번역이
    M2M-100 이면 Gemma 는 없어도 됩니다. 예전에는 whisper 를 늘 필수로 적어 두어,
    가벼운 엔진만 쓰려는 사람에게도 845MB 를 받으라고 띠를 세웠습니다.

      - Silero VAD: 어떤 전사기든 구간을 자릅니다
      - 기본 전사 엔진의 모델 파일
      - M2M-100: 번역 대체 경로가 늘 뒤에 둡니다 (기본 번역기가 M2M 이면 그 자체)
      - Gemma: 기본 번역 엔진이 gemma 일 때만
      - ffmpeg: 시스템에 있으면 status 가 `system` 으로 답하므로 받을 필요는 없습니다
    """
    cfg = cfg or config.load()
    need = ["silero-vad"]
    asr = config.find("asr", config.active("asr", cfg), cfg) or {}
    if asr.get("backend", "tcpp") == "tcpp":
        e = _entry_for_file(asr.get("model") or "whisper-large-v3-turbo-Q8_0.gguf")
        if e:
            need.append(e["id"])
    need.append("m2m100")
    tr = config.find("tr", config.active("tr", cfg), cfg) or {}
    if tr.get("backend") == "gemma":
        e = _entry_for_file(tr.get("model_path") or "gemma-4-E4B_q4_0-it.gguf")
        if e:
            need.append(e["id"])
    need.append("ffmpeg")
    return need


def overview() -> dict:
    need = set(required_ids())
    items = []
    for e in entries():
        st = status(e)
        st["required"] = e["id"] in need        # 표의 고정값이 아니라 지금 설정 기준
        items.append(st)
    items += other_files()
    ready = all(i["state"] in ("ready", "system") for i in items if i.get("required"))
    cfg = config.load()
    return {"items": items, "ready": ready, "required": sorted(need),
            "setup_done": config.setup_done(cfg),
            "asr_active": config.active("asr", cfg), "active": config.active("tr", cfg),
            "model_dir": paths.model_dir(), "tools_dir": paths.tools_dir(),
            "frozen": paths.frozen(), "platform": f"{sys.platform}/{platform.machine()}",
            "hf_token": bool(os.environ.get("HF_TOKEN"))}


def setup_options() -> dict:
    """초기 설정 화면이 보여 줄 선택지. 엔진마다 어떤 모델이 필요하고 몇 MB 인지,
    이미 있는지를 붙입니다 -- 고르는 순간 무엇을 받게 되는지 보여야 합니다."""
    cfg = config.load()

    def describe(kind, b):
        out = {"id": b["id"], "label": b.get("label") or b["id"], "backend": b.get("backend"),
               "device": b.get("device", "auto"), "model": None}
        e = None
        if kind == "asr" and b.get("backend", "tcpp") == "tcpp":
            e = _entry_for_file(b.get("model") or "whisper-large-v3-turbo-Q8_0.gguf")
        elif kind == "tr" and b.get("backend") == "gemma":
            e = _entry_for_file(b.get("model_path") or "gemma-4-E4B_q4_0-it.gguf")
        elif kind == "tr" and b.get("backend") == "local":
            e = find("m2m100")
        if e:
            st = status(e)
            out["model"] = {"id": e["id"], "label": e["label"], "size": e.get("size"),
                            "state": st["state"], "purpose": e.get("purpose")}
        return out

    return {"asr": [describe("asr", b) for b in config.entries("asr", cfg)],
            "tr": [describe("tr", b) for b in config.entries("tr", cfg)],
            "asr_active": config.active("asr", cfg), "active": config.active("tr", cfg),
            "setup_done": config.setup_done(cfg)}


def apply_setup(asr_id: str, tr_id: str, start_download: bool = True) -> dict:
    """초기 설정을 적용합니다: 기본 엔진 둘을 정하고, 그 조합에 필요한 것을 받기 시작합니다."""
    for kind, eid in (("asr", asr_id), ("tr", tr_id)):
        if eid:
            got = config.set_active(kind, eid)
            if "error" in got:
                return got
    config.mark_setup_done()
    result = {"ok": True, "required": required_ids()}
    if start_download:
        result.update(download([i for i in required_ids() if i != "ffmpeg"
                                or status(find("ffmpeg"))["state"] == "missing"]))
    return result


# ---- 내려받기 ------------------------------------------------------------------

def _publish(entry_id: str):
    e = find(entry_id)
    if e is not None:
        st = status(e)
        # 화면은 이 알림으로 그 줄을 통째로 갈아 끼우므로, overview()와 같은 기준의
        # 「필수」 표시를 실어야 합니다. 표의 고정값을 보내면 받는 중인 기본 전사기가
        # 필수 아닌 것으로 바뀌어 보였습니다.
        try:
            st["required"] = entry_id in set(required_ids())
        except Exception:
            pass
        bus.publish({"type": "model", **st})


def _open(url: str, headers: dict | None = None, timeout: float = 60.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def _hf_headers(token: str | None = None) -> dict:
    tok = token or os.environ.get("HF_TOKEN") or ""
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _fetch_file(entry_id: str, url: str, dest: str, headers: dict, gz: bool = False,
                total_hint: int = 0, base_done: int = 0, base_total: int = 0):
    """주소 하나를 `dest`로. `.part`에 받고 끝나면 이름을 바꿉니다.

    `.part`가 있으면 `Range`로 이어 받습니다. 서버가 206이 아니라 200으로
    답하면(범위를 모르거나 파일이 바뀐 경우) 처음부터 다시 받습니다. gzip은
    풀면서 씁니다 -- 그때는 전체 크기를 모르므로 받은 양만 알립니다.

    `base_*`는 여러 파일로 된 모델에서 앞 파일들까지 합친 진행을 내기 위한 것입니다.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    have = os.path.getsize(part) if (os.path.exists(part) and not gz) else 0
    req_headers = dict(headers)
    if have:
        req_headers["Range"] = f"bytes={have}-"
    try:
        resp = _open(url, req_headers, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have:           # 이미 다 받은 조각입니다
            os.replace(part, dest)
            return
        raise RuntimeError(f"HTTP {exc.code} {exc.reason} ({url})") from exc
    with resp:
        if have and resp.status != 206:
            have = 0                            # 이어 받기를 못 하니 처음부터
        length = resp.headers.get("Content-Length")
        total = (int(length) + have) if (length and not gz) else (total_hint or 0)
        mode = "ab" if have else "wb"
        src = gzip.GzipFile(fileobj=resp) if gz else resp
        done = have
        last = 0.0
        with open(part, mode) as f:
            while True:
                with _lock:
                    if entry_id in _cancel:
                        raise _Cancelled()
                buf = src.read(CHUNK)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                now = time.time()
                if now - last >= PROGRESS_EVERY_S:
                    last = now
                    with _lock:
                        _progress[entry_id] = {"state": "downloading",
                                               "done": base_done + done,
                                               "total": base_total or total,
                                               "current": os.path.basename(dest)}
                    _publish(entry_id)
    if total and not gz and done < total:
        raise RuntimeError(f"받다 끊겼습니다 ({done}/{total} 바이트). 다시 시작하면 이어 받습니다.")
    os.replace(part, dest)


def _hf_files(repo: str, headers: dict) -> list[str]:
    with _open(f"{HF}/api/models/{repo}", headers, timeout=30) as r:
        info = json.load(r)
    names = [s.get("rfilename") for s in info.get("siblings", []) if s.get("rfilename")]
    # 저장소 안내문과 git 잡동사니는 받지 않습니다.
    return [n for n in names if not n.startswith(".") and n not in ("README.md",)]


def _hf_size(repo: str, file: str, headers: dict) -> int:
    """파일 크기를 HEAD로 묻습니다. 없는 파일이면 예외 -- 추가 전 검사에 씁니다."""
    req = urllib.request.Request(f"{HF}/{repo}/resolve/main/{file}", method="HEAD",
                                 headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=30) as r:
        # 큰 파일은 CDN으로 넘겨지고, 원래 크기는 이 헤더에 남습니다.
        return int(r.headers.get("X-Linked-Size") or r.headers.get("Content-Length") or 0)


class _Cancelled(Exception):
    pass


def _download_one(entry: dict, token: str | None):
    eid = entry["id"]
    dest = target_path(entry)
    with _lock:
        _progress[eid] = {"state": "downloading", "done": 0, "total": entry.get("size") or 0}
    _publish(eid)
    if entry.get("tool"):
        url, gz = _tool_asset(entry["tool"])
        _fetch_file(eid, url, dest, {}, gz=gz, total_hint=entry.get("size") or 0)
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        import stream
        stream.reset_tool_cache()               # 다음 호출부터 새 도구를 봅니다
        return
    headers = _hf_headers(token) if entry.get("repo") else {}
    if entry.get("dir"):
        os.makedirs(dest, exist_ok=True)
        marker = os.path.join(dest, ".incomplete")
        open(marker, "w").close()
        files = _hf_files(entry["repo"], headers)
        if not files:
            raise RuntimeError(f"{entry['repo']}에 파일이 없습니다")
        sizes = {}
        for name in files:
            try:
                sizes[name] = _hf_size(entry["repo"], name, headers)
            except Exception:
                sizes[name] = 0
        total = sum(sizes.values())
        done = 0
        for name in files:
            fdest = os.path.join(dest, name)
            if os.path.isfile(fdest) and sizes.get(name) and os.path.getsize(fdest) == sizes[name]:
                done += sizes[name]
                continue
            _fetch_file(eid, f"{HF}/{entry['repo']}/resolve/main/{name}", fdest, headers,
                        base_done=done, base_total=total)
            done += sizes.get(name, 0)
        os.remove(marker)
        return
    url = entry.get("url") or f"{HF}/{entry['repo']}/resolve/main/{entry['file']}"
    _fetch_file(eid, url, dest, headers, total_hint=entry.get("size") or 0)


def _after_download(entry: dict):
    """받은 뒤에 할 일. 사용자가 추가한 모델은 엔진 설정에 항목을 넣습니다."""
    if not entry.get("custom") or not entry.get("register"):
        return
    reg = entry["register"]
    kind = reg.get("kind")
    if kind == "asr":
        config.upsert("asr", {"id": reg["id"], "label": reg.get("label") or entry["label"],
                              "backend": "tcpp", "model": entry["file"],
                              "device": reg.get("device") or "auto"})
    elif kind == "tr":
        config.upsert("tr", {"id": reg["id"], "label": reg.get("label") or entry["label"],
                             "backend": "gemma", "model_path": entry["file"],
                             "device": reg.get("device") or "auto", "min_chars": 0})


def _loop():
    while True:
        item = _queue.get()
        if item is None:
            return
        eid, token = item
        with _lock:
            if eid in _queued:
                _queued.remove(eid)
            # 줄에 서 있는 동안 취소된 항목은 받지 않고 넘어갑니다. 예전에는 여기서
            # 표시를 무조건 지워 버려, 「중단」을 누른 것이 그대로 받아졌습니다.
            cancelled = eid in _cancel
            _cancel.discard(eid)
        if cancelled:
            _publish(eid)
            continue
        entry = find(eid)
        if entry is None:
            continue
        try:
            _download_one(entry, token)
            _after_download(entry)
            with _lock:
                _progress.pop(eid, None)
            print(f"[models] 받았습니다: {entry['label']}", file=sys.stderr, flush=True)
        except _Cancelled:
            with _lock:
                _progress.pop(eid, None)
                _cancel.discard(eid)
            print(f"[models] 중단: {entry['label']} (받은 만큼은 .part로 남습니다)",
                  file=sys.stderr, flush=True)
        except Exception as exc:
            with _lock:
                _progress[eid] = {"state": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
            print(f"[models] 실패: {entry['label']}: {exc}", file=sys.stderr, flush=True)
        _publish(eid)


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, daemon=True, name="models-download")
        _worker.start()


def download(ids: list[str], token: str | None = None) -> dict:
    """항목들을 내려받기 줄에 세웁니다. 이미 있는 것과 줄에 선 것은 건너뜁니다."""
    queued, skipped, unknown = [], [], []
    for mid in ids:
        e = find(mid)
        if e is None:
            unknown.append(mid)
            continue
        st = status(e)
        if st["state"] in ("ready", "downloading", "queued"):
            skipped.append(mid)
            continue
        with _lock:
            _progress.pop(mid, None)            # 지난 실패는 지웁니다
            _queued.append(mid)
        _queue.put((mid, token))
        queued.append(mid)
        _publish(mid)
    _ensure_worker()
    return {"queued": queued, "skipped": skipped, "unknown": unknown}


def default_ids(with_gemma: bool = False) -> list[str]:
    """설치 스크립트가 받는 기본 세트: 지금 설정에 필요한 것 + 작은 것들.

    기본 설정은 가벼운 CPU 엔진(SenseVoice Small + M2M-100)이라 여기서 Gemma 나
    whisper 를 받지 않습니다 -- 6GB 를 받게 해 놓고 사양이 안 되는 기계에서 돌지
    않는 것보다, 일단 도는 것을 받고 화면에서 올리는 편이 낫습니다. `with_gemma` 는
    예전 스크립트 옵션과의 호환용입니다.
    """
    out = [i for i in required_ids() if i != "ffmpeg"]
    for extra in ("moonshine-base", "campplus"):
        if extra not in out:
            out.append(extra)
    if with_gemma and "gemma-4-e4b" not in out:
        out.append("gemma-4-e4b")
    return out


def cancel(model_id: str) -> dict:
    with _lock:
        if model_id in _queued:
            _queued.remove(model_id)
            _progress.pop(model_id, None)
            # 큐 안의 항목은 그대로 두되, 작업 스레드가 꺼냈을 때 `_queued`에
            # 없으면 받지 않게 하기는 어렵습니다 -- 대신 취소 표시를 남겨 첫
            # 조각에서 멈추게 합니다.
            _cancel.add(model_id)
        elif (_progress.get(model_id) or {}).get("state") == "downloading":
            _cancel.add(model_id)
        else:
            return {"error": "받는 중이 아닙니다"}
    _publish(model_id)
    return {"cancelled": model_id}


def _safe_name(name: str) -> str | None:
    """모델 디렉터리 **바로 아래**의 이름 하나로 쓸 수 있는 문자열인지. 아니면 None.

    `os.path.basename`만 믿으면 안 됩니다 -- `..`과 `.`을 그대로 돌려줍니다. 그래서
    `delete("file:..")`이 모델 디렉터리의 **부모**, 곧 사용자 영역 전체(묶음이면 설정과
    자막 DB까지)를 `rmtree`할 수 있었습니다. 이름은 구분자 없이 한 조각이어야 하고,
    실제 경로가 모델 디렉터리 안에 있어야 합니다.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name:
        return None
    if os.path.basename(name) != name or name.startswith("."):
        return None
    # 마지막 조각은 realpath 로 풀지 않습니다 -- 링크라면 가리키는 곳이 밖일 수 있는데,
    # 우리는 링크 자체(모델 디렉터리 안의 항목)를 다루는 것이고 delete 가 링크는 링크만
    # 지우도록 따로 처리합니다. 조각이 하나이므로 normpath 로 충분합니다.
    root = os.path.realpath(paths.model_dir())
    if not os.path.normpath(os.path.join(root, name)).startswith(root + os.sep):
        return None
    return name


def _catalog_names() -> set[str]:
    """목록이 관리하는 파일·디렉터리 이름(+.part). 이것들은 정식 id로만 다룹니다."""
    out = {CUSTOM_FILE}
    for e in entries():
        base = os.path.basename(target_path(e))
        out.add(base)
        out.add(base + ".part")
    return out


def delete(model_id: str) -> dict:
    """모델(또는 도구) 파일을 지웁니다. 받다 만 `.part`도 함께."""
    if model_id.startswith("file:"):
        name = _safe_name(model_id[5:])
        if name is None or name in _catalog_names():
            return {"error": "지울 수 없는 이름입니다"}
        p = os.path.join(paths.model_dir(), name)
        if not os.path.lexists(p):
            return {"error": "그런 파일이 없습니다"}
        if os.path.islink(p):
            os.remove(p)                    # 링크는 링크만. 가리키는 곳을 비우지 않습니다
        else:
            _remove(p)
        return {"deleted": model_id}
    e = find(model_id)
    if e is None:
        return {"error": f"'{model_id}' 항목이 없습니다"}
    st = status(e)
    if st["state"] in ("downloading", "queued"):
        return {"error": "받는 중입니다. 먼저 중단하십시오."}
    path = target_path(e)
    freed = 0
    for p in (path, path + ".part"):
        if os.path.exists(p):
            freed += _dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
            try:
                _remove(p)
            except OSError as exc:
                # 윈도우는 쓰고 있는 파일을 지우지 못합니다 -- 올라와 있는 모델이
                # 그렇습니다. 서버를 다시 켠 뒤에 지우라고 말합니다.
                return {"error": f"지우지 못했습니다: {exc}. 이 모델이 올라와 있으면 "
                                 "서버를 다시 켠 뒤 지우십시오."}
    if e.get("custom"):
        rest = [c for c in custom_entries() if c["id"] != model_id]
        _save_custom(rest)
        reg = e.get("register") or {}
        if reg.get("kind") and reg.get("id"):
            config.delete(reg["kind"], reg["id"])
    with _lock:
        _progress.pop(model_id, None)
    _publish(model_id)
    return {"deleted": model_id, "freed_mb": round(freed / 1e6, 1)}


def _remove(p: str):
    if os.path.isdir(p):
        shutil.rmtree(p)
    else:
        os.remove(p)


def add_custom(kind: str, repo: str, file: str, label: str = "", engine_id: str = "",
               device: str = "auto", token: str | None = None) -> dict:
    """허깅페이스의 GGUF 하나를 목록에 넣고 곧 받기 시작합니다.

    `kind`는 asr(전사, transcribe.cpp GGUF) 또는 tr(번역, llama.cpp 채팅 GGUF).
    받은 뒤 `backends.json`에 엔진 항목을 넣어 화면의 선택기에 나타나게 합니다.
    파일이 실재하는지는 HEAD로 먼저 물어봅니다 -- 5초 뒤 "404"보다 지금 아는
    편이 낫습니다.
    """
    repo = (repo or "").strip().strip("/")
    file = (file or "").strip()
    kind = (kind or "").strip()
    if kind not in ("asr", "tr"):
        return {"error": "종류는 asr(전사) 또는 tr(번역)이어야 합니다"}
    if repo.count("/") != 1 or _safe_name(file) is None:
        return {"error": "저장소는 `소유자/이름`, 파일은 그 저장소 안의 파일 이름이어야 합니다"}
    if not file.lower().endswith(".gguf"):
        return {"error": "GGUF 파일만 받습니다 (.gguf)"}
    if file in _catalog_names():
        # 정식 모델과 같은 이름으로 받으면 그 파일을 덮고, 나중에 이 항목을 지울 때
        # 정식 모델까지 사라집니다.
        return {"error": f"'{file}'은 기본 목록의 모델과 이름이 같습니다. 그쪽 항목을 쓰십시오."}
    base = os.path.splitext(file)[0]
    engine_id = (engine_id or "").strip() or ("hf-" + "".join(
        c if c.isalnum() else "-" for c in base.lower()).strip("-"))[:40]
    if config.find(kind, engine_id):
        return {"error": f"'{engine_id}' 엔진 id가 이미 있습니다. 다른 id를 적으십시오."}
    model_id = "custom:" + engine_id
    if find(model_id):
        return {"error": f"'{model_id}' 항목이 이미 있습니다"}
    headers = _hf_headers(token)
    try:
        size = _hf_size(repo, file, headers)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"error": f"접근이 막혔습니다 (HTTP {exc.code}). 라이선스 동의가 필요한 "
                             "저장소라면 HF_TOKEN을 주십시오."}
        return {"error": f"파일을 찾지 못했습니다 (HTTP {exc.code}): {repo}/{file}"}
    except Exception as exc:
        return {"error": f"허깅페이스에 묻지 못했습니다: {exc}"}
    entry = {"id": model_id, "kind": kind, "label": label or base,
             "purpose": f"{'전사' if kind == 'asr' else '번역'} · 허깅페이스 {repo}",
             "file": file, "repo": repo, "size": size, "required": False, "default": False,
             "register": {"kind": kind, "id": engine_id, "label": label or base,
                          "device": device or "auto"}}
    with _lock:
        custom = custom_entries()
        custom.append(entry)
        _save_custom(custom)
    got = download([model_id], token)
    return {"added": model_id, "engine": engine_id, "size": size, **got}


# ---- 명령줄 (설치 스크립트가 부릅니다) ------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="모델·도구를 내려받고 상태를 봅니다.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="목록과 상태")
    d = sub.add_parser("download", help="받습니다. 'default' 는 기본 세트, 'all' 은 전부")
    d.add_argument("ids", nargs="+")
    d.add_argument("--with-gemma", action="store_true", help="기본 세트에 Gemma(4.9GB)를 넣습니다")
    d.add_argument("--skip-gemma", action="store_true",
                   help="(예전 옵션, 지금은 기본이 그렇습니다)")
    d.add_argument("--with-tools", action="store_true",
                   help="ffmpeg·yt-dlp 독립 실행 파일도 받습니다 (시스템에 없을 때)")
    x = sub.add_parser("delete", help="지웁니다")
    x.add_argument("id")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for it in overview()["items"]:
            marks = {"ready": "✓", "system": "✓", "missing": "·", "partial": "…"}
            mark = marks.get(it["state"], "?")
            extra = f"  (시스템: {it['system']})" if it.get("system") else ""
            print(f"  {mark} {it['id']:<26} {it['state']:<8} {it['label']}{extra}")
        return 0
    if args.cmd == "delete":
        r = delete(args.id)
        print(r)
        return 1 if "error" in r else 0

    ids: list[str] = []
    for want in args.ids:
        if want == "default":
            ids += default_ids(with_gemma=args.with_gemma)
        elif want == "all":
            ids += [e["id"] for e in entries() if not e.get("tool")]
        else:
            ids.append(want)
    if args.with_tools:
        for t in ("ffmpeg", "yt-dlp"):
            if status(find(t))["state"] == "missing":
                ids.append(t)
    # 터미널에서는 진행을 줄로 찍습니다. bus 구독자는 없어도 됩니다.
    last = {}

    def show(ev):
        if ev.get("type") != "model":
            return
        s = ev["state"]
        if s == "downloading":
            tot = ev.get("total") or 0
            pct = f"{ev['done'] / tot * 100:5.1f}%" if tot else f"{ev['done'] / 1e6:,.0f}MB"
            line = f"\r  {ev['label']:<36} {pct}"
            if last.get(ev["id"]) != line:
                print(line.ljust(70), end="", file=sys.stderr, flush=True)
                last[ev["id"]] = line
        elif s == "ready":
            print(f"\r  ✓ {ev['label']}".ljust(70), file=sys.stderr, flush=True)
        elif s == "error":
            print(f"\r  ✗ {ev['label']}: {ev.get('error')}".ljust(70), file=sys.stderr, flush=True)

    q = bus.subscribe()
    got = download(ids)
    for mid in got["skipped"]:
        print(f"  · {find(mid)['label']} 있음", file=sys.stderr)
    for mid in got["unknown"]:
        print(f"  ? 모르는 항목: {mid}", file=sys.stderr)
    pending = set(got["queued"])
    failed = []
    while pending:
        try:
            ev = json.loads(q.get(timeout=1.0))
        except queue.Empty:
            continue
        show(ev)
        if ev.get("type") == "model" and ev["id"] in pending and ev["state"] in ("ready", "error"):
            pending.discard(ev["id"])
            if ev["state"] == "error":
                failed.append(ev["id"])
    bus.unsubscribe(q)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
