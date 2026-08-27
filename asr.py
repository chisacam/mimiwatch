"""Transcription backends: hayamimi locally, or an external ASR service.

hayamimi stays the default -- it is free, private, and runs at 38-88x
realtime on this machine. The reason to reach outside is quality on hard
audio: the 2026-08-27 samples showed conference talks transcribing cleanly
while a four-way VTuber broadcast with overlapping speakers lost lines
entirely.

Both backends must return the same thing: cues carrying media-relative
timestamps, because the player aligns subtitles by looking them up against
getCurrentTime(), not by estimating.

The external path is the OpenAI /v1/audio/transcriptions shape (Whisper and
its many compatible servers). A full broadcast is far past any request size
limit, so the audio is cut into windows at silent points and each window's
returned segment times are shifted by that window's offset.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import urllib.request
import uuid
import wave

import numpy as np

HAYAMIMI = os.environ.get("HAYAMIMI_DIR", "/Users/chiyak/hobby/hayamimi")
sys.path.insert(0, os.path.join(HAYAMIMI, "scripts"))

SAMPLE_RATE = 16000


class ASRBackend:
    name = "base"

    def transcribe(self, samples: np.ndarray, lang: str | None,
                   on_progress=None) -> list[dict]:
        raise NotImplementedError


class LocalHayamimi(ASRBackend):
    """The default: VAD-segment locally and decode with RoutedASR."""

    name = "local-hayamimi"

    def transcribe(self, samples, lang, on_progress=None, speakers=False):
        import transcribe_vod as vod
        return vod.transcribe(samples, lang, on_progress=on_progress,
                              speakers=speakers)


class OpenAICompatibleASR(ASRBackend):
    """POST windows of audio to /v1/audio/transcriptions.

    `verbose_json` gives per-segment timestamps relative to the window; the
    window offset restores them to media time. Windows are cut on a low-energy
    frame so a sentence is not sliced in half.
    """

    name = "openai-asr"

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 window_s: float = 240.0, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.window_s = window_s
        self.timeout = timeout

    # ---- window splitting -------------------------------------------------
    def _cut_points(self, samples: np.ndarray) -> list[tuple[int, int]]:
        win = int(self.window_s * SAMPLE_RATE)
        if len(samples) <= win:
            return [(0, len(samples))]
        # Look for the quietest 100ms frame in the 20s before each nominal
        # boundary, so the cut lands in a pause rather than mid-word.
        frame = int(0.1 * SAMPLE_RATE)
        search = int(20 * SAMPLE_RATE)
        spans, start = [], 0
        while start < len(samples):
            nominal = start + win
            if nominal >= len(samples):
                spans.append((start, len(samples)))
                break
            lo = max(start + frame, nominal - search)
            region = samples[lo:nominal]
            n = len(region) // frame
            if n:
                energies = np.abs(region[:n * frame].reshape(n, frame)).mean(axis=1)
                cut = lo + int(np.argmin(energies)) * frame
            else:
                cut = nominal
            spans.append((start, cut))
            start = cut
        return spans

    def _wav_bytes(self, samples: np.ndarray) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
        return buf.getvalue()

    def _post(self, audio: bytes, lang: str | None) -> dict:
        boundary = uuid.uuid4().hex
        parts = []

        def field(name, value):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                         f'name="{name}"\r\n\r\n{value}\r\n'.encode())

        field("model", self.model)
        field("response_format", "verbose_json")
        if lang:
            field("language", lang)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="file"; filename="audio.wav"\r\n'
                     f'Content-Type: audio/wav\r\n\r\n'.encode())
        parts.append(audio)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/v1/audio/transcriptions",
                                     data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)

    def transcribe(self, samples, lang, on_progress=None):
        spans = self._cut_points(samples)
        cues: list[dict] = []
        total = len(samples)
        for start, end in spans:
            data = self._post(self._wav_bytes(samples[start:end]), lang)
            offset = start / SAMPLE_RATE
            segments = data.get("segments")
            if segments:
                for sg in segments:
                    text = (sg.get("text") or "").strip()
                    if text:
                        cues.append({"start": round(offset + float(sg["start"]), 3),
                                     "end": round(offset + float(sg["end"]), 3),
                                     "lang": lang or data.get("language", "") or "",
                                     "text": text})
            elif (data.get("text") or "").strip():
                # A server that only returns plain text costs us the timing
                # inside the window; anchor the whole thing at its start
                # rather than dropping it.
                cues.append({"start": round(offset, 3),
                             "end": round(end / SAMPLE_RATE, 3),
                             "lang": lang or "", "text": data["text"].strip()})
            if on_progress:
                on_progress(end / total)
        return cues


def build(spec: dict | None) -> ASRBackend:
    spec = spec or {}
    if spec.get("backend") == "openai":
        return OpenAICompatibleASR(spec["base_url"], spec["model"],
                                   spec.get("api_key", ""),
                                   float(spec.get("window_s", 240)))
    return LocalHayamimi()
