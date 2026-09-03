"""The one place that downloads and manages models and run tools (ffmpeg, yt-dlp).

The two install scripts (install.sh, install.ps1) each used to carry their own
list of model URLs, and there was no way to see from the screen what was there
and what was not. With the model file missing, a session ended in
`FileNotFoundError` and all that was left was the words
"./install.sh 를 실행하십시오". Shipped as a bundle (PyInstaller), not even
those scripts are there.

Three things happen here.

  - **The catalogue (CATALOG)**: what is fetched from where. The install scripts
    and the screen look at the same table.
  - **State**: present · missing · half-fetched (`.part`) · downloading · failed.
    The screen's "Models & Tools" shows it as it is, and progress is pushed over
    `bus`.
  - **Downloading**: one worker thread fetches in the order things queued up.
    Fetching two 5GB files side by side does not get any faster and only makes
    the progress display a mess. It is fetched into `.part` and renamed once
    fully fetched -- so an interrupted fetch does not look finished, and it
    resumes with `Range`.

Models always live in `stream.model_dir()` (= `paths.model_dir()`), tools in
`paths.tools_dir()`. Outside the repository. Swapping the bundle for a new build
or deleting the repository does not fetch 6GB again.

**Fetching straight from Hugging Face.** To use a GGUF outside the default
catalogue, the repository id and the file name are written down (`add_custom`).
After the fetch an engine entry is put into `backends.json` so that it appears in
the screen's picker. A transcription model has to be a GGUF that transcribe.cpp
reads, and a translation model a chat model that llama.cpp reads -- that cannot
be checked here, so it shows up the first time it is used after the fetch.

`huggingface_hub` is not used. All that is needed is the one
`resolve/main/<file>` URL and, for a model made of several files, the file list
(`api/models/<repo>`), so the standard library is enough and the bundle gets
that much lighter.
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
# A statically built single-file ffmpeg executable. macOS (arm64·x64), Windows and Linux
# are in one place and the build is pinned by tag, so the URL does not wobble. It is a
# GPL build, but we are not the ones distributing it -- the user fetches it. That is why
# it is not put into the bundle.
FFMPEG_STATIC = "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
YTDLP_LATEST = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"

USER_AGENT = "mimiwatch/1.0 (+https://github.com/chisacam/mimiwatch)"
CHUNK = 1 << 20
# Progress notifications go out only at this interval. When 100MB a second is coming in,
# notifying every 1MB is noise.
PROGRESS_EVERY_S = 0.4

# The sizes are rough values for display (bytes). The real size is known from
# Content-Length at fetch time.
CATALOG: list[dict] = [
    {"id": "silero-vad", "kind": "aux", "label": "Silero VAD",
     "purpose_en": "Speech segmentation · required for transcription",
     "purpose_ko": "발화 구간 분할 · 전사에 반드시 필요", "file": "silero_vad.onnx",
     "url": f"{GH_SHERPA}/asr-models/silero_vad.onnx", "size": 643_854,
     "required": True, "default": True},
    {"id": "silero-vad-v5", "kind": "aux", "label": "Silero VAD v5",
     "purpose_en": "Speech segmentation · newer build, robust to noise "
                   "(used with MIMIWATCH_VAD_MODEL=silero_vad_v5.onnx)",
     "purpose_ko": "발화 구간 분할 · 잡음에 강한 새 판 "
                   "(MIMIWATCH_VAD_MODEL=silero_vad_v5.onnx 로 씀)",
     "file": "silero_vad_v5.onnx", "url": f"{GH_SHERPA}/asr-models/silero_vad_v5.onnx",
     "size": 2_313_101, "required": False, "default": False},
    {"id": "whisper-large-v3-turbo", "kind": "asr", "label": "Whisper large-v3-turbo Q8_0",
     "purpose_en": "Transcription (all languages) · quality · GPU recommended",
     "purpose_ko": "전사 (모든 언어) · 품질 · GPU 권장",
     "file": "whisper-large-v3-turbo-Q8_0.gguf",
     "repo": "handy-computer/whisper-large-v3-turbo-gguf", "size": 886_381_760,
     "required": True, "default": True},
    {"id": "sensevoice-small", "kind": "asr", "label": "SenseVoice Small Q8_0",
     "purpose_en": "Transcription · default (light · CPU, 8x faster than whisper)",
     "purpose_ko": "전사 · 기본 (가벼움 · CPU, whisper의 8배 빠름)",
     "file": "SenseVoiceSmall-Q8_0.gguf",
     "repo": "handy-computer/SenseVoiceSmall-gguf", "size": 252_684_608,
     "required": False, "default": True},
    {"id": "moonshine-base", "kind": "asr", "label": "Moonshine base Q8_0",
     "purpose_en": "Light transcription · English only (74MB, same quality as whisper)",
     "purpose_ko": "가벼운 전사 · 영어 전용 (74MB, 품질은 whisper와 같음)",
     "file": "moonshine-base-Q8_0.gguf",
     "repo": "handy-computer/moonshine-base-gguf", "size": 77_476_480,
     "required": False, "default": True},
    {"id": "gemma-4-e4b", "kind": "tr", "label": "Gemma 4 E4B q4_0",
     "purpose_en": "Translation · quality · GPU recommended "
                   "(4.9GB, a wide quality gap over M2M-100)",
     "purpose_ko": "번역 · 품질 · GPU 권장 (4.9GB, M2M-100과 품질 차이가 큼)",
     "file": "gemma-4-E4B_q4_0-it.gguf",
     "repo": "google/gemma-4-E4B-it-qat-q4_0-gguf", "size": 5_154_941_280,
     "required": False, "default": True},
    {"id": "m2m100", "kind": "tr", "label": "M2M-100 (CTranslate2)",
     "purpose_en": "Translation · default (light · CPU) · also serves as Gemma's fallback",
     "purpose_ko": "번역 · 기본 (가벼움 · CPU) · Gemma의 대체용도 겸함",
     "dir": "mojicast-m2m100-ct2",
     "repo": "ishiki-emo/mojicast-m2m100-ct2", "size": 473_000_000,
     "required": True, "default": True},
    {"id": "campplus", "kind": "aux", "label": "CAM++",
     "purpose_en": "Speaker tags for VODs (optional)",
     "purpose_ko": "녹화본 화자 태그 (선택)", "file": "campplus_sv.onnx",
     "url": f"{GH_SHERPA}/speaker-recongition-models/"
            "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
     "size": 28_281_164, "required": False, "default": True},
    # Tools. There is no need to fetch one the system has -- the state says so.
    {"id": "ffmpeg", "kind": "tool", "label": "ffmpeg",
     "purpose_en": "Audio extraction · needed only when the system has none",
     "purpose_ko": "오디오 풀기 · 시스템에 없을 때만 필요", "tool": "ffmpeg",
     "size": 45_000_000, "required": True, "default": False},
    {"id": "yt-dlp", "kind": "tool", "label": "yt-dlp",
     "purpose_en": "Video URL resolution · the self-updating build. Recommended for the bundle",
     "purpose_ko": "영상 주소 해석 · 스스로 판올림하는 판. 묶음으로 쓸 때 권장",
     "tool": "yt-dlp", "size": 37_000_000, "required": False, "default": False},
    # The JS runtime YouTube has required since 2025.11. Public live streams (HLS) do
    # without it, but the VOD and cookie (members-only) paths lose their formats when it is
    # missing. If the system has deno, that one.
    {"id": "deno", "kind": "tool", "label": "deno",
     "purpose_en": "JS runtime YouTube extraction needs · recommended for VODs "
                   "and members-only streams",
     "purpose_ko": "유튜브 추출에 필요한 JS 런타임 · 녹화본·멤버십 방송에 권장",
     "tool": "deno", "size": 45_000_000, "required": False, "default": False},
]

# Models the user added straight from Hugging Face. Kept inside the model directory --
# because it has to have the same lifetime as the models.
CUSTOM_FILE = "custom.json"

_lock = threading.Lock()
_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_progress: dict[str, dict] = {}        # progress/error of what is downloading · failed
_cancel: set[str] = set()
_queued: list[str] = []


# ---- The catalogue ---------------------------------------------------------

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


DENO_LATEST = "https://github.com/denoland/deno/releases/latest/download"


def _tool_asset(name: str) -> tuple[str, bool]:
    """(URL, whether gzipped). One executable for this platform. A zip URL is
    unpacked by `_download_one`."""
    arch = platform.machine().lower()
    if name == "deno":
        if sys.platform == "darwin":
            a = "aarch64" if arch in ("arm64", "aarch64") else "x86_64"
            return f"{DENO_LATEST}/deno-{a}-apple-darwin.zip", False
        if sys.platform == "win32":
            return f"{DENO_LATEST}/deno-x86_64-pc-windows-msvc.zip", False
        return f"{DENO_LATEST}/deno-x86_64-unknown-linux-gnu.zip", False
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
    """Where this entry lands once it is complete."""
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
    """The current state of one entry."""
    path = target_path(entry)
    # `purpose` stays in the list next to the pair: a custom.json written before
    # the split carries only the plain key, and the screen falls back to it.
    out = {k: entry.get(k) for k in ("id", "kind", "label", "purpose_en", "purpose_ko",
                                     "purpose", "size", "required",
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
        # No need to fetch what the system has. Which one is actually used is written down too.
        system = shutil.which(entry["tool"]) or next(
            (os.path.join(d, entry["tool"]) for d in paths._EXTRA_PATH
             if os.path.isfile(os.path.join(d, entry["tool"]))), None)
        out["system"] = system
        if out["state"] != "ready" and system:
            out["state"] = "system"
    if prog.get("state") == "error" and out["state"] != "ready":
        # A failure stays until it is fetched again or deleted. Even with a half-fetched
        # piece there, it has to show as "Failed" for why it stopped to be visible --
        # pressing "Again" resumes it.
        out["state"] = "error"
        out["error"] = prog.get("error")
    return out


def other_files() -> list[dict]:
    """Files in the model directory that are not in the catalogue. Hand-placed GGUFs and
    the like -- they have to be deletable, and their name can be written into a
    transcription engine's config and used."""
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
                    "purpose_en": "File not in the catalogue",
                    "purpose_ko": "목록에 없는 파일", "path": p, "have": size, "size": size,
                    "state": "ready", "file": name})
    return out


def _entry_for_file(name: str) -> dict | None:
    """Finds a catalogue entry by file name (or directory name)."""
    base = os.path.basename(name)
    for e in entries():
        if e.get("file") == base or e.get("dir") == base:
            return e
    return None


def required_ids(cfg: dict | None = None) -> list[str]:
    """What **must** be there to run with the current config.

    It is not a fixed table. If the default transcriber is SenseVoice, whisper need not be
    there; if the translation is M2M-100, Gemma need not be there. whisper used to be
    written down as always required, which raised a banner telling even someone who only
    wanted the light engines to fetch 845MB.

      - Silero VAD: every transcriber cuts segments
      - the model file of the default transcription engine
      - M2M-100: the translation fallback path always keeps it behind (itself, when the
        default translator is M2M)
      - Gemma: only when the default translation engine is gemma
      - ffmpeg: no need to fetch it when the system has it, since status answers `system`
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
        st["required"] = e["id"] in need        # by the current config, not the table's fixed value
        items.append(st)
    items += other_files()
    ready = all(i["state"] in ("ready", "system") for i in items if i.get("required"))
    cfg = config.load()
    if ready and not config.setup_done(cfg):
        # Someone who was using this before the first-run marker existed: if everything
        # needed is there, count it as done. Otherwise, when one thing goes missing later,
        # "First run" comes up.
        cfg = config.mark_setup_done()
    return {"items": items, "ready": ready, "required": sorted(need),
            "setup_done": config.setup_done(cfg),
            "asr_active": config.active("asr", cfg), "active": config.active("tr", cfg),
            "model_dir": paths.model_dir(), "tools_dir": paths.tools_dir(),
            "frozen": paths.frozen(), "platform": f"{sys.platform}/{platform.machine()}",
            "hf_token": bool(os.environ.get("HF_TOKEN"))}


def setup_options() -> dict:
    """The choices the first-run screen will show. For each engine it attaches which model
    is needed and how many MB it is, and whether it is already there -- the moment you
    pick, it has to show what you will be fetching."""
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
                            "state": st["state"],
                            "purpose_en": e.get("purpose_en"),
                            "purpose_ko": e.get("purpose_ko"),
                            "purpose": e.get("purpose")}
        return out

    return {"asr": [describe("asr", b) for b in config.entries("asr", cfg)],
            "tr": [describe("tr", b) for b in config.entries("tr", cfg)],
            "asr_active": config.active("asr", cfg), "active": config.active("tr", cfg),
            "setup_done": config.setup_done(cfg)}


def apply_setup(asr_id: str, tr_id: str, start_download: bool = True) -> dict:
    """Applies the first-run setup: fixes the two default engines, and starts fetching
    what that combination needs."""
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


# ---- Downloading -----------------------------------------------------------

def _publish(entry_id: str):
    e = find(entry_id)
    if e is not None:
        st = status(e)
        # The screen replaces that whole row from this notification, so it has to carry a
        # "required" mark on the same basis as overview(). Sending the table's fixed value
        # made the default transcriber that was downloading look as if it had turned
        # non-required.
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
    """One URL into `dest`. It is fetched into `.part` and renamed when it is done.

    When a `.part` is there, it resumes with `Range`. If the server answers 200
    rather than 206 (it does not understand ranges, or the file changed), the fetch
    starts over from the beginning. gzip is written while being unpacked -- the
    total size is unknown then, so only the amount fetched is reported.

    `base_*` is there to report progress that adds in the earlier files of a model made
    of several files.
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
        if exc.code == 416 and have:           # the piece is already fully fetched
            os.replace(part, dest)
            return
        raise RuntimeError(f"HTTP {exc.code} {exc.reason} ({url})") from exc
    with resp:
        if have and resp.status != 206:
            have = 0                            # no resume, so from the beginning
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
    # The repository's readme and git odds and ends are not fetched.
    return [n for n in names if not n.startswith(".") and n not in ("README.md",)]


def _hf_size(repo: str, file: str, headers: dict) -> int:
    """Asks the file size with HEAD. A file that is not there raises -- used as the
    check before adding."""
    req = urllib.request.Request(f"{HF}/{repo}/resolve/main/{file}", method="HEAD",
                                 headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=30) as r:
        # Big files are handed off to a CDN, and the original size is left in this header.
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
        if url.endswith(".zip"):
            # A release with one executable inside a zip (deno). The zip is fetched and
            # only that file is taken out.
            import zipfile
            zpath = dest + ".zip"
            _fetch_file(eid, url, zpath, {}, total_hint=entry.get("size") or 0)
            want = os.path.basename(dest)
            with zipfile.ZipFile(zpath) as z:
                names = [n for n in z.namelist() if os.path.basename(n) == want]
                if not names:
                    raise RuntimeError(f"zip 안에 {want} 가 없습니다: {z.namelist()[:5]}")
                with z.open(names[0]) as src, open(dest + ".part", "wb") as out:
                    shutil.copyfileobj(src, out)
            os.remove(zpath)
            os.replace(dest + ".part", dest)
        else:
            _fetch_file(eid, url, dest, {}, gz=gz, total_hint=entry.get("size") or 0)
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        import stream
        stream.reset_tool_cache()               # the next call sees the new tool
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
    """What to do after a fetch. A model the user added gets an entry put into the
    engine config."""
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
            # An entry cancelled while it stood in the queue is skipped rather than
            # fetched. This used to clear the mark unconditionally here, so what "Stop"
            # had been pressed on got fetched anyway.
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
    """Puts entries into the download queue. What is already there and what is already
    in the queue are skipped."""
    queued, skipped, unknown = [], [], []
    for mid in ids:
        e = find(mid)
        if e is None:
            unknown.append(mid)
            continue
        # The state check and the queuing inside one lock. If two quick requests queue
        # the same thing twice, the second wipes the finished file and fetches it again.
        with _lock:
            already = mid in _queued or (_progress.get(mid) or {}).get("state") == "downloading"
        if already or status(e)["state"] == "ready":
            skipped.append(mid)
            continue
        with _lock:
            if mid in _queued:
                skipped.append(mid)
                continue
            _progress.pop(mid, None)            # a past failure is cleared
            _queued.append(mid)
        _queue.put((mid, token))
        queued.append(mid)
        _publish(mid)
    _ensure_worker()
    return {"queued": queued, "skipped": skipped, "unknown": unknown}


def default_ids(with_gemma: bool = False) -> list[str]:
    """The default set the install scripts fetch: what the current config needs + the
    small ones.

    The default config is the light CPU engines (SenseVoice Small + M2M-100), so Gemma
    and whisper are not fetched here -- rather than have 6GB fetched and then not run on
    a machine whose specs do not take it, it is better to fetch what does run and move up
    from the screen. `with_gemma` is there for compatibility with the old script option.
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
            # The item in the queue is left as it is, but making it not be fetched
            # because it is no longer in `_queued` when the worker thread pulls it out
            # is hard -- instead a cancel mark is left so that it stops on the first
            # chunk.
            _cancel.add(model_id)
        elif (_progress.get(model_id) or {}).get("state") == "downloading":
            _cancel.add(model_id)
        else:
            return {"error": "받는 중이 아닙니다"}
    _publish(model_id)
    return {"cancelled": model_id}


def _safe_name(name: str) -> str | None:
    """Whether the string can serve as one name **directly under** the model directory.
    None if not.

    `os.path.basename` alone must not be trusted -- it returns `..` and `.` as they are.
    That let `delete("file:..")` `rmtree` the model directory's **parent**, that is the
    whole user area (with a bundle, the config and the subtitle DB too). The name has to
    be one piece with no separator, and the real path has to be inside the model
    directory.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name:
        return None
    if os.path.basename(name) != name or name.startswith("."):
        return None
    # The last piece is not resolved with realpath -- with a link, where it points may be
    # outside, but what we handle is the link itself (an entry inside the model directory)
    # and delete treats a link separately so that only the link is removed. With a single
    # piece, normpath is enough.
    root = os.path.realpath(paths.model_dir())
    if not os.path.normpath(os.path.join(root, name)).startswith(root + os.sep):
        return None
    return name


def _catalog_names() -> set[str]:
    """The file and directory names the catalogue manages (+.part). These are handled
    by their proper id only."""
    out = {CUSTOM_FILE}
    for e in entries():
        base = os.path.basename(target_path(e))
        out.add(base)
        out.add(base + ".part")
    return out


def delete(model_id: str) -> dict:
    """Deletes a model (or tool) file. A half-fetched `.part` goes with it."""
    if model_id.startswith("file:"):
        name = _safe_name(model_id[5:])
        if name is None or name in _catalog_names():
            return {"error": "지울 수 없는 이름입니다"}
        p = os.path.join(paths.model_dir(), name)
        if not os.path.lexists(p):
            return {"error": "그런 파일이 없습니다"}
        if os.path.islink(p):
            os.remove(p)                    # for a link, only the link. Where it points stays
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
                # Windows cannot delete a file that is in use -- a model that is loaded
                # is one. We tell them to delete it after starting the server again.
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
    """Puts one GGUF from Hugging Face into the catalogue and starts fetching it at once.

    `kind` is asr (transcription, a transcribe.cpp GGUF) or tr (translation, a
    llama.cpp chat GGUF). After the fetch an engine entry is put into
    `backends.json` so that it appears in the screen's picker. Whether the file
    really exists is asked with HEAD first -- knowing now is better than a "404"
    five seconds later.
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
        # Fetching under the same name as a catalogue model overwrites that file, and
        # deleting this entry later makes the catalogue model disappear with it.
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
             "purpose_en": f"{'Transcription' if kind == 'asr' else 'Translation'}"
                           f" · Hugging Face {repo}",
             "purpose_ko": f"{'전사' if kind == 'asr' else '번역'} · 허깅페이스 {repo}",
             "file": file, "repo": repo, "size": size, "required": False, "default": False,
             "register": {"kind": kind, "id": engine_id, "label": label or base,
                          "device": device or "auto"}}
    with _lock:
        custom = custom_entries()
        custom.append(entry)
        _save_custom(custom)
    got = download([model_id], token)
    return {"added": model_id, "engine": engine_id, "size": size, **got}


# ---- Command line (the install scripts call it) ----------------------------

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
    # In a terminal the progress is printed as lines. No bus subscriber is needed.
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
