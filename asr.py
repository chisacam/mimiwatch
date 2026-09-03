"""Transcription backends: the local GGUF runtime, or an external ASR service.

로컬(transcribe.cpp의 Whisper)이 기본입니다 -- 무료이고 밖으로 나가지 않으며
이 기계에서 38~88배속입니다. 밖에 손을 뻗는 이유는 어려운 소리의 품질입니다:
2026-08-27 표본에서 발표는 깨끗이 받아 적혔지만 네 명이 겹쳐 말하는 방송은
줄을 통째로 잃었습니다.

외부 경로는 OpenAI 의 `/v1/audio/transcriptions` 모양입니다(Whisper 와 그 호환
서버들). 두 갈래로 씁니다.

  - 녹화본(`OpenAICompatibleASR`): 방송 전체는 어떤 요청 크기 제한도 넘으므로
    조용한 지점에서 몇 분짜리 창으로 잘라 보내고, 창마다 돌아온 구간 시각에
    창의 시작을 더합니다.
  - 라이브(`OpenAIStreamASR`): VAD 가 잘라 낸 발화 한 조각(2~12초)을 그때그때
    보냅니다. 로컬 엔진과 같은 표면(`transcribe(samples, sr, …)`)을 내놓아
    `run_stream`/`Refiner`가 어느 쪽인지 모르게 합니다. 지연은 왕복 시간만큼
    늘고, 그것은 사용자가 고른 거래입니다 -- 무료 로컬 모델보다 나은 인식을
    비용을 내고 사려는 사람이 있습니다.

Both must return cues carrying media-relative timestamps, because the player
aligns subtitles by looking them up against getCurrentTime(), not by
estimating.

이름에 남아 있던 "hayamimi"는 이 프로젝트가 처음 전사 엔진으로 빌려 쓰던
저장소입니다. 지금은 그 코드에 기대지 않으므로 이름도 함께 걷어냈습니다.
옛 설정(`local-hayamimi`)은 그대로 기본 엔진으로 읽힙니다.
"""
from __future__ import annotations

import io
import json
import time
import urllib.parse
import urllib.request
import uuid
import wave

import numpy as np

import stream

SAMPLE_RATE = 16000

# OpenAI 의 verbose_json 은 언어를 코드가 아니라 이름("japanese")으로 줍니다.
# 자막에는 코드가 실려야 번역기가 알아봅니다.
LANG_NAMES = {"japanese": "ja", "korean": "ko", "english": "en", "chinese": "zh",
              "mandarin": "zh", "spanish": "es", "french": "fr", "german": "de",
              "russian": "ru", "portuguese": "pt", "italian": "it", "vietnamese": "vi",
              "indonesian": "id", "thai": "th", "arabic": "ar", "hindi": "hi"}


def lang_code(value: str | None) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    return LANG_NAMES.get(v, v if len(v) <= 3 else "")


def wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def post_transcription(base_url: str, model: str, api_key: str, audio: bytes,
                       lang: str | None, timeout: float) -> dict:
    """`/v1/audio/transcriptions`에 wav 하나를 보내고 verbose_json 을 받습니다.

    multipart 를 손으로 짓습니다 -- 표준 라이브러리만 쓰는 서버라 requests 가
    없고, 칸이 넷뿐이라 라이브러리를 들일 만한 일이 아닙니다.
    """
    boundary = uuid.uuid4().hex
    parts = []

    def field(name, value):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="{name}"\r\n\r\n{value}\r\n'.encode())

    field("model", model)
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
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(f"{base_url.rstrip('/')}/v1/audio/transcriptions",
                                 data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


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

    구간 분할과 시각 계산은 로컬 경로와 똑같이 VAD가 맡고, 해독만 갈아
    끼웁니다.
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
    """POST windows of audio to /v1/audio/transcriptions (녹화본).

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

    def _post(self, audio: bytes, lang: str | None) -> dict:
        return post_transcription(self.base_url, self.model, self.api_key, audio, lang,
                                  self.timeout)

    def transcribe(self, samples, lang, on_progress=None, should_stop=None):
        spans = self._cut_points(samples)
        cues: list[dict] = []
        total = len(samples)
        for start, end in spans:
            # 창 하나가 몇십 초 분량이므로 사이에서 확인하면 충분합니다.
            if should_stop and should_stop():
                raise stream.Cancelled()
            data = self._post(wav_bytes(samples[start:end]), lang)
            offset = start / SAMPLE_RATE
            segments = data.get("segments")
            got_lang = lang or lang_code(data.get("language"))
            if segments:
                for sg in segments:
                    text = (sg.get("text") or "").strip()
                    if text:
                        cues.append({"start": round(offset + float(sg["start"]), 3),
                                     "end": round(offset + float(sg["end"]), 3),
                                     "lang": got_lang, "text": text})
            elif (data.get("text") or "").strip():
                # A server that only returns plain text costs us the timing
                # inside the window; anchor the whole thing at its start
                # rather than dropping it.
                cues.append({"start": round(offset, 3),
                             "end": round(end / SAMPLE_RATE, 3),
                             "lang": got_lang, "text": data["text"].strip()})
            if on_progress:
                on_progress(end / total)
        return cues


class OpenAIStreamASR:
    """라이브용: VAD 가 잘라 낸 발화 한 조각을 그때그때 원격에 보냅니다.

    `tcpp_asr.TranscribeCppASR`와 같은 표면입니다 -- `run_stream`과 `Refiner`가
    부르는 것은 `transcribe(samples, sample_rate, speech_s=…, live=…)` 하나와
    `forced_lang`·`label`뿐이라, 이 둘이 같으면 어느 쪽이 들어가도 세션은
    모릅니다. 갈아 끼우는 일은 `tcpp_asr.LiveASR`가 맡습니다.

    실패는 예외로 냅니다. `_drain`이 연달아 다섯 번까지는 그 조각만 버리고
    넘어가고, 그 뒤에는 세션을 놓습니다 -- 로컬 엔진의 규칙과 같습니다.
    """

    name = "openai-asr"

    def __init__(self, spec: dict, lang: str | None):
        if not spec.get("base_url") or not spec.get("model"):
            raise ValueError("OpenAI 호환 전사 엔진에는 base_url 과 model 이 필요합니다")
        self.base_url = spec["base_url"].rstrip("/")
        self.model = spec["model"]
        self.api_key = spec.get("api_key", "")
        # 조각 하나는 길어야 12초입니다. 30초 안에 답이 없으면 그 조각은 버립니다.
        self.timeout = float(spec.get("timeout") or 30.0)
        self.forced_lang = lang or ""
        self.min_switch_s = 0.0
        host = urllib.parse.urlsplit(self.base_url).netloc or self.base_url
        self.label = spec.get("label") or f"{self.model} @ {host}"
        self.device = "remote"
        self.threads = 0
        self.hallucinations = 0

    # --- RoutedASR 표면 -------------------------------------------------------
    punct = None
    ko_spacer = None

    def resident_models(self) -> list[str]:
        return [self.label]

    def reset_session(self):
        pass

    def _identify_lang(self, samples, sample_rate) -> str:
        return self.forced_lang

    identify = _identify_lang

    def _replace(self, text: str) -> str:
        return text

    def partial(self, samples, sample_rate, lang_hint=None) -> str:
        return ""

    def transcribe(self, samples: np.ndarray, sample_rate: int,
                   known_lang: str | None = None, speech_s: float | None = None,
                   live: bool = True, segments: bool = False) -> dict:
        """`segments`(구간 시각)는 받아서 무시합니다.

        이 원격 표면은 발화 한 조각을 통째로 보내고 글자만 돌려받습니다.
        부르는 쪽은 「달라고 했는데 안 왔다」를 이미 다뤄야 하므로 -- 원격
        서버가 verbose_json 을 안 줄 수도 있습니다 -- 여기서 예외를 내는
        것보다 빈 손으로 돌아서는 편이 낫습니다.
        """
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"16kHz만 지원합니다 (받은 값 {sample_rate})")
        from tcpp_asr import looks_hallucinated
        t0 = time.perf_counter()
        data = post_transcription(self.base_url, self.model, self.api_key,
                                  wav_bytes(np.asarray(samples, dtype=np.float32)),
                                  self.forced_lang or None, self.timeout)
        decode_ms = (time.perf_counter() - t0) * 1000
        text = (data.get("text") or "").strip()
        if not text and data.get("segments"):
            text = " ".join((sg.get("text") or "").strip() for sg in data["segments"]).strip()
        if looks_hallucinated(text):
            self.hallucinations += 1
            print(f"[환각 차단] {self.label}: {text[:50]}", flush=True)
            text = ""
        return {"text": text, "lang": self.forced_lang or lang_code(data.get("language")),
                "tier": self.label, "lid_ms": 0.0, "decode_ms": decode_ms, "probe_ms": 0.0}


def build(spec: dict | None) -> ASRBackend:
    spec = spec or {}
    if spec.get("backend") == "tcpp":
        return TranscribeCpp(spec)
    if spec.get("backend") == "openai":
        return OpenAICompatibleASR(spec["base_url"], spec["model"],
                                   spec.get("api_key", ""),
                                   float(spec.get("window_s", 240)))
    return DefaultLocal()


