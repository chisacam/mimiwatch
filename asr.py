"""Transcription backends: the local GGUF runtime, or an external ASR service.

로컬(transcribe.cpp의 Whisper)이 기본입니다 -- 무료이고 밖으로 나가지 않으며
이 기계에서 38~88배속입니다. 밖에 손을 뻗는 이유는 어려운 소리의 품질입니다:
2026-08-27 표본에서 발표는 깨끗이 받아 적혔지만 네 명이 겹쳐 말하는 방송은
줄을 통째로 잃었습니다.

이름에 남아 있던 "hayamimi"는 이 프로젝트가 처음 전사 엔진으로 빌려 쓰던
저장소입니다. 지금은 그 코드에 기대지 않으므로 이름도 함께 걷어냈습니다.
옛 설정(`local-hayamimi`)은 그대로 기본 엔진으로 읽힙니다.

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
import urllib.request
import uuid
import wave

import numpy as np

import stream

SAMPLE_RATE = 16000


class ASRBackend:
    name = "base"

    def transcribe(self, samples: np.ndarray, lang: str | None,
                   on_progress=None, should_stop=None) -> list[dict]:
        """`should_stop`이 참을 돌려주면 `stream.Cancelled`를 냅니다.

        전사는 녹화본 작업에서 가장 긴 단계입니다. 단계 사이에서만
        확인하면 시작한 뒤로는 끝날 때까지 멈출 수 없습니다.
        """
        raise NotImplementedError


DEFAULT_NAME = "default"
# 옛 설정과 요청이 쓰던 이름. 같은 것으로 읽습니다.
LEGACY_DEFAULT_IDS = ("local-hayamimi",)


class DefaultLocal(ASRBackend):
    """The default: VAD-segment locally and decode with the bundled Whisper."""

    name = DEFAULT_NAME

    def transcribe(self, samples, lang, on_progress=None, speakers=False,
                   should_stop=None):
        import transcribe_vod as vod
        return vod.transcribe(samples, lang, on_progress=on_progress,
                              speakers=speakers, should_stop=should_stop)


LocalHayamimi = DefaultLocal       # 옛 이름. 시험과 스크립트가 부를 수 있습니다.


class TranscribeCpp(ASRBackend):
    """transcribe.cpp의 GGUF 모델로 구간을 해독합니다.

    구간 분할과 시각 계산은 로컬 경로와 똑같이 hayamimi의 VAD가 맡고,
    해독만 갈아 끼웁니다. 배치된 모델이 없는 언어면 build_live_asr가
    RoutedASR을 돌려주므로 이 경로도 자동으로 기본 엔진으로 돌아갑니다.
    """

    name = "tcpp"

    def __init__(self, spec: dict):
        self.spec = spec

    def transcribe(self, samples, lang, on_progress=None, speakers=False,
                   should_stop=None):
        import transcribe_vod as vod
        from tcpp_asr import build_live_asr
        engine = build_live_asr(self.spec, lang, threads=4)
        return vod.transcribe(samples, lang, on_progress=on_progress,
                              speakers=speakers, asr=engine,
                              should_stop=should_stop)


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

    def transcribe(self, samples, lang, on_progress=None, should_stop=None):
        spans = self._cut_points(samples)
        cues: list[dict] = []
        total = len(samples)
        for start, end in spans:
            # 창 하나가 몇십 초 분량이므로 사이에서 확인하면 충분합니다.
            if should_stop and should_stop():
                raise stream.Cancelled()
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
    if spec.get("backend") == "tcpp":
        return TranscribeCpp(spec)
    if spec.get("backend") == "openai":
        return OpenAICompatibleASR(spec["base_url"], spec["model"],
                                   spec.get("api_key", ""),
                                   float(spec.get("window_s", 240)))
    return DefaultLocal()
