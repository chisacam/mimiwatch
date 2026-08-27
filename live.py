"""Live sessions: a running broadcast into subtitles the browser can follow.

Built on what the 2026-08-27 measurements settled:

  - yt-dlp resolves a live audio-only HLS rendition, and ffmpeg decodes it to
    the 16kHz mono PCM hayamimi already accepts.
  - The manifest publishes EXT-X-PROGRAM-DATE-TIME and EXT-X-MEDIA-SEQUENCE,
    so each captured sample has a media position -- subtitles can be placed
    on the player's own clock instead of guessed at.
  - The refine pass waits for two seconds of silence, which a fast talker
    never gives: it lagged up to 20s, on 40-50% of lines. So a final is
    published the moment it exists and the refined version REPLACES it later,
    carrying the same id. Waiting for refine would leave the screen empty.
  - Subtitles leave over SSE. hayamimi's WebSocket mirror dropped events
    (92 delivered vs 15 over the same window), so it is not used here.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

import numpy as np

HAYAMIMI = os.environ.get("HAYAMIMI_DIR", "/Users/chiyak/hobby/hayamimi")
sys.path.insert(0, os.path.join(HAYAMIMI, "scripts"))

from asr_engine import RoutedASR                      # noqa: E402
from realtime_transcribe import (AudioHistory, PartialPrinter,  # noqa: E402
                                 Refiner, SessionStats, build_vad, run_stream)

import translate as mw_translate                      # noqa: E402
from tcpp_asr import build_live_asr                   # noqa: E402

SAMPLE_RATE = 16000
CHUNK = 1600            # 0.1s per VAD feed

# hayamimi's 12s force-split suits a single speaker who eventually pauses.
# A multi-speaker broadcast never gives the VAD its 0.35s of silence: on a
# four-way Minecraft collab, 44% of segments ran to the 12s cap, averaging
# 10.1s, and a ten-second stretch of overlapping speech decodes to one short
# line -- which is exactly the "they are talking but no subtitle appears"
# complaint. Splitting sooner produces more, shorter, more accurate finals;
# the refine pass merges them back for the reading panel.
LIVE_MAX_SPEECH = 4.0
LIVE_MIN_SILENCE = 0.30

# How aggressively to force-split, by what the audio actually sounds like.
# Measured on 2026-08-27: a four-way collab at 12s produced 5.0 lines/min
# against the 8.2 its own recording managed, because 44% of segments ran to
# the cap; at 6s it recovered to 7.9. A lecture has real pauses and suffers
# from splitting a sentence in half, so it keeps the longer cap.
PROFILES = {
    "talk":      {"max_speech": 12.0, "min_silence": 0.35,
                  "label": "발표·강연 (한 사람이 문장 사이에 쉼)"},
    "interview": {"max_speech": 6.0,  "min_silence": 0.35,
                  "label": "대담·인터뷰 (번갈아 말하고 쉼이 있음)"},
    "broadcast": {"max_speech": 4.0,  "min_silence": 0.30,
                  "label": "일반 방송 (한두 사람, 쉼이 짧음)"},
    "collab":    {"max_speech": 3.0,  "min_silence": 0.25,
                  "label": "합방·다인 대화 (발화가 겹침)"},
}

_sessions: dict[str, "LiveSession"] = {}
_finished: dict[str, dict] = {}     # id -> last status, so the UI can still ask
_lock = threading.Lock()
MAX_FINISHED = 20


def resolve_audio(url: str) -> tuple[str, dict]:
    """Audio-only rendition plus what the manifest says about media time."""
    for fmt in ("234", "233", "bestaudio"):
        out = subprocess.run(["yt-dlp", "--no-warnings", "-f", fmt, "-g", url],
                             capture_output=True, text=True)
        lines = out.stdout.strip().splitlines()
        if out.returncode == 0 and lines:
            return lines[0], manifest_info(lines[0])
    raise RuntimeError("yt-dlp could not resolve an audio stream for this URL")


def manifest_info(m3u8: str) -> dict:
    """Where in the broadcast the playlist starts, and how long it is.

    MEDIA-SEQUENCE is NOT a media position. YouTube serves some live streams
    with a full-DVR playlist: 2496 one-second segments covering the whole
    41 minutes so far, numbered from 0. Multiplying sequence by segment
    length gave 0 there and looked like a parse failure, when the real
    problem was that the playlist -- and therefore ffmpeg -- starts at the
    beginning of the broadcast rather than at its live edge.

    So the window length is what matters: it says how far behind live the
    first segment sits, which is what `-live_start_index` has to skip.
    """
    info = {"seq": None, "target": None, "pdt": None,
            "segments": 0, "window_s": 0.0, "media_base": 0.0}
    try:
        with urllib.request.urlopen(m3u8, timeout=10) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:
        return info
    durations = []
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            info["seq"] = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            info["target"] = float(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:") and info["pdt"] is None:
            info["pdt"] = line.split(":", 1)[1].strip()
        elif line.startswith("#EXTINF:"):
            try:
                durations.append(float(line.split(":", 1)[1].rstrip(",").split(",")[0]))
            except ValueError:
                pass
    info["segments"] = len(durations)
    info["window_s"] = sum(durations)
    return info


def media_base_from(pdt: str | None, release_ts: float | None,
                    window_s: float) -> float:
    """Media position of the audio we are about to receive.

    PDT is the wall clock of the playlist's first segment and
    release_timestamp is when the broadcast began, so their difference is
    the media offset. We skip to the live edge, so add the window we chose
    not to read.
    """
    if not pdt or not release_ts:
        return 0.0
    try:
        stamp = pdt.replace("Z", "+00:00")
        first = __import__("datetime").datetime.fromisoformat(stamp).timestamp()
    except Exception:
        return 0.0
    return max(0.0, (first - release_ts) + window_s)


class Sink:
    """Stands in for hayamimi's SubtitleServer.

    run_stream and Refiner publish through `printer.server`, so implementing
    its three methods is enough to divert the whole pipeline into a queue
    without touching hayamimi.
    """

    def __init__(self, session: "LiveSession"):
        self.s = session

    def partial(self, text: str):
        self.s.emit({"type": "partial", "text": text})

    def final(self, text, lang="", speaker="", latency_ms=None, tier=""):
        self.s.publish_line("final", text, lang, speaker)

    def publish(self, event: dict):
        if event.get("type") == "refine":
            self.s.publish_line("refine", event.get("text", ""),
                                event.get("lang", ""), event.get("speaker", ""))


class LiveSession:
    def __init__(self, url: str, lang: str | None, viewer_lang: str,
                 backend_id: str, profile: str = "broadcast",
                 asr_backend_id: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.lang = lang
        self.viewer_lang = viewer_lang
        self.backend_id = backend_id
        self.asr_backend_id = asr_backend_id
        prof = PROFILES.get(profile, PROFILES["broadcast"])
        self.profile = profile if profile in PROFILES else "broadcast"
        self.max_speech = prof["max_speech"]
        self.min_silence = prof["min_silence"]
        self.state = "starting"
        self.error: str | None = None
        self.title = ""
        self.media_base = 0.0        # media seconds at the first sample we get
        self.window_s = 0.0          # DVR window we skipped to reach live
        self.audio_s = 0.0           # seconds fed so far
        self.started = time.time()
        self.lines = 0
        self.translated = 0
        self._seq = 0
        self._subs: list[queue.Queue] = []
        self._stop = threading.Event()
        self._ff: subprocess.Popen | None = None
        self._asr = None            # released on stop; see _release()
        self._tr = None
        # Refine replaces the final that covered the same speech. Matching on
        # the text hayamimi already emitted is enough here because a refined
        # group repeats its members' words.
        self._recent: list[dict] = []

    # ---- fan-out ----------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with _lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with _lock:
            if q in self._subs:
                self._subs.remove(q)

    def emit(self, event: dict):
        data = json.dumps(event, ensure_ascii=False)
        with _lock:
            for q in self._subs:
                q.put(data)

    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error,
                "title": self.title, "url": self.url,
                "source_lang": self.lang, "viewer_lang": self.viewer_lang,
                "backend": self.backend_id,
                "asr_backend": self.asr_backend_id,
                "asr": getattr(self._asr, "label", "hayamimi"),
                "media_base": round(self.media_base, 2),
                "profile": self.profile, "max_speech": self.max_speech,
                "window_s": round(self.window_s, 1),
                "audio_s": round(self.audio_s, 1),
                "elapsed": round(time.time() - self.started, 1),
                "lines": self.lines, "translated": self.translated}

    # ---- publishing -------------------------------------------------------
    def publish_line(self, kind: str, text: str, lang: str, speaker: str):
        text = (text or "").strip()
        if not text:
            return
        media_t = self.media_base + self.audio_s
        if kind == "final":
            self._seq += 1
            self.lines += 1
            cue = {"type": "cue", "id": self._seq, "kind": "final",
                   "t": round(media_t, 2), "text": text,
                   "lang": lang, "speaker": speaker}
            self._recent.append(cue)
            del self._recent[:-40]
            self.emit(cue)
            self._translate_async(cue)
        else:
            # A refined group supersedes the finals whose words it contains.
            covered = [c for c in self._recent
                       if c["kind"] == "final" and c["text"][:8] in text]
            target = covered[0] if covered else None
            cue = {"type": "cue", "id": target["id"] if target else self._seq,
                   "kind": "refine", "t": round(target["t"] if target else media_t, 2),
                   "text": text, "lang": lang, "speaker": speaker,
                   "replaces": [c["id"] for c in covered]}
            for c in covered:
                self._recent.remove(c)
            self.emit(cue)
            self._translate_async(cue)

    def _translate_async(self, cue: dict):
        if self.lang and self.lang == self.viewer_lang:
            return
        threading.Thread(target=self._translate, args=(cue,), daemon=True).start()

    def _translate(self, cue: dict):
        try:
            src = cue.get("lang") or self.lang or ""
            if not src or src == self.viewer_lang:
                return
            if not self._tr.should_translate(cue["text"], src, self.viewer_lang):
                return
            out = self._tr.translate(cue["text"], src, self.viewer_lang)
            if out and out.strip() != cue["text"].strip():
                self.translated += 1
                self.emit({"type": "translation", "id": cue["id"],
                           "kind": cue["kind"], "text": out})
        except Exception as exc:
            print(f"[live] translate failed: {exc}", file=sys.stderr)

    # ---- pipeline ---------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()
        if self._ff:
            self._ff.terminate()

    def _release(self):
        """Drop the models this session loaded.

        Each session builds its own RoutedASR and translator -- roughly 3GB
        resident once the Japanese recogniser and M2M-100 are in. Holding a
        finished session in the registry kept all of that alive: seven
        sessions in one afternoon reached 23GB.
        """
        self._asr = None
        self._tr = None
        self._recent.clear()
        if self._ff:
            try:
                self._ff.kill()
            except Exception:
                pass
            self._ff = None

    def _chunks(self):
        assert self._ff and self._ff.stdout
        need = CHUNK * 2
        while not self._stop.is_set():
            raw = self._ff.stdout.read(need)
            if not raw or len(raw) < need:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            self.audio_s += len(samples) / SAMPLE_RATE
            yield samples
        self.state = "stopped"

    def _run(self):
        try:
            meta = subprocess.run(["yt-dlp", "--no-warnings", "-j", self.url],
                                  capture_output=True, text=True)
            if meta.returncode == 0:
                d = json.loads(meta.stdout)
                self.title = d.get("title", "")
                if not d.get("is_live"):
                    self.state = "error"
                    self.error = "라이브가 아닙니다. 녹화본은 영상 추가로 처리하십시오."
                    self.emit({"type": "status", **self.status()})
                    return

            src, info = resolve_audio(self.url)
            release_ts = None
            if meta.returncode == 0:
                release_ts = d.get("release_timestamp") or d.get("timestamp")
            # Skipping the DVR window means the audio starts at the live edge;
            # media_base has to account for everything we deliberately passed.
            self.window_s = info.get("window_s") or 0.0
            self.media_base = media_base_from(info.get("pdt"), release_ts,
                                              self.window_s)
            print(f"[live] playlist: {info.get('segments')} segments / "
                  f"{self.window_s:.0f}s window, media_base={self.media_base:.0f}s",
                  flush=True)
            self.state = "loading"
            self.emit({"type": "status", **self.status()})

            spec = asr_spec = None
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "backends.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            for b in cfg["backends"]:
                if b["id"] == self.backend_id:
                    spec = b
            for b in cfg.get("asr_backends", []):
                if b["id"] == self.asr_backend_id:
                    asr_spec = b
            self._tr = mw_translate.build(spec)

            # Speaker tags are a recorded-video feature. CAM++ needs enough
            # voice in one segment to place it, and live splits at 3-4s to
            # keep up with a talker who rarely finishes a long sentence: in
            # 70 seconds that produced six speaker ids on a stream that did
            # not have six people talking. A label that invents speakers is
            # worse than no label.
            asr = self._asr = build_live_asr(asr_spec, self.lang, threads=4)
            vad = build_vad(min_silence=self.min_silence,
                            max_speech=self.max_speech)
            sink = Sink(self)
            printer = PartialPrinter(enabled=True, server=sink)
            history = AudioHistory(SAMPLE_RATE)
            refiner = Refiner(asr, history, SAMPLE_RATE, printer)

            # -live_start_index -2 starts two segments from the end of the
            # playlist. Without it ffmpeg reads a full-DVR playlist from the
            # top and transcribes the broadcast's opening greetings while the
            # viewer watches its live edge.
            self._ff = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error",
                 "-live_start_index", "-2", "-i", src,
                 "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
                stdout=subprocess.PIPE)

            self.state = "running"
            self.emit({"type": "status", **self.status()})
            run_stream(self._chunks(), vad, SAMPLE_RATE, asr, SessionStats(),
                       printer, refiner, history)
            self.state = "stopped"
            self.emit({"type": "status", **self.status()})
        except Exception as exc:
            self.state = "error"
            self.error = str(exc)[:300]
            self.emit({"type": "status", **self.status()})
        finally:
            del asr, vad, printer, history, refiner
            self._release()
            _retire(self.id)


def start(url: str, lang: str | None, viewer_lang: str, backend_id: str,
          profile: str = "broadcast", asr_backend_id: str = "") -> dict:
    # One viewer watches one broadcast. Leaving the previous session running
    # would keep a second copy of every model resident for nothing.
    for old_id in list(_sessions):
        old = get(old_id)
        if old is not None:
            old.stop()

    s = LiveSession(url, lang, viewer_lang, backend_id, profile=profile,
                    asr_backend_id=asr_backend_id)
    with _lock:
        _sessions[s.id] = s
    s.start()
    return {"id": s.id}


def _retire(session_id: str):
    """Move a finished session out of the live registry, keeping only its
    final status so a late poll still gets an answer instead of a 404."""
    with _lock:
        s = _sessions.pop(session_id, None)
        if s is not None:
            _finished[session_id] = s.status()
            for old in list(_finished)[:-MAX_FINISHED]:
                _finished.pop(old, None)


def get(session_id: str) -> LiveSession | None:
    with _lock:
        return _sessions.get(session_id)


def status_of(session_id: str) -> dict | None:
    s = get(session_id)
    if s is not None:
        return s.status()
    with _lock:
        return _finished.get(session_id)


def set_backend(session_id: str, backend_id: str) -> dict:
    """Swap the translator mid-session. Lines already published keep the text
    they were given; everything after this uses the new backend."""
    s = get(session_id)
    if not s:
        return {"error": "no such session"}
    spec = None
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "backends.json"), encoding="utf-8") as f:
        for b in json.load(f)["backends"]:
            if b["id"] == backend_id:
                spec = b
    if spec is None:
        return {"error": f"'{backend_id}' 백엔드가 없습니다"}
    s._tr = mw_translate.build(spec)
    s.backend_id = backend_id
    return {"backend": backend_id}


def stop(session_id: str) -> dict:
    s = get(session_id)
    if not s:
        return {"error": "no such session"}
    s.stop()
    return {"state": "stopping"}
