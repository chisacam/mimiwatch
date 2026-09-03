"""Transcription backends: the local GGUF runtime, or an external ASR service.

Local (transcribe.cpp's Whisper) is the default -- it is free, nothing goes
outside, and it runs at 38~88x on this machine. The reason to reach outside is
quality on difficult audio: in the 2026-08-27 sample a talk was written down
cleanly, but a stream with four people talking over each other lost whole lines.

The external path has the shape of OpenAI's `/v1/audio/transcriptions` (Whisper
and the servers compatible with it). It is used two ways.

  - VOD (`OpenAICompatibleASR`): a whole stream goes over any request size limit,
    so it is cut at a quiet point into windows of a few minutes and sent, and the
    window's start is added to the segment timestamps that come back for each
    window.
  - Live (`OpenAIStreamASR`): one utterance slice the VAD cut out (2~12 s) is sent
    as it comes. It puts out the same surface as the local engine
    (`transcribe(samples, sr, …)`) so that `run_stream`/`Refiner` cannot tell
    which is which. The latency grows by the round trip, and that is the trade
    the user chose -- there are people willing to pay for recognition better than
    a free local model.

Both must return cues carrying media-relative timestamps, because the player
aligns subtitles by looking them up against getCurrentTime(), not by
estimating.

The "hayamimi" that lingered in the names is the repository this project first
borrowed as its transcription engine. It no longer leans on that code, so the
name was cleared away with it. The old config (`local-hayamimi`) still reads as
the default engine.
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

# OpenAI's verbose_json gives the language as a name ("japanese"), not a code.
# The subtitle has to carry the code for the translator to recognise it.
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
    """Sends one wav to `/v1/audio/transcriptions` and gets verbose_json back.

    The multipart body is built by hand -- this is a server that uses the standard
    library only, so there is no requests, and with just four fields it is not
    worth bringing a library in.
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
                   on_progress=None, speakers: bool = False,
                   should_stop=None, refine: bool = True) -> list[dict]:
        """Raises `stream.Cancelled` when `should_stop` returns true.

        Transcription is the longest stage of a VOD job. Checking only between
        stages means it cannot be stopped once started until it ends.

        What `speakers` and `refine` can do differs per transcriber. The caller
        (`jobs`) used to look at the engine name to decide whether to pass them,
        and that test was `name == "default"`, so speaker tags never once reached
        the local transcriber (`tcpp`) chosen in the config -- meaning the
        screen's "Attach speaker tags" did nothing at all on the default engine.
        So they stand on the surface instead, and a transcriber that cannot do
        them quietly ignores them.
        """
        raise NotImplementedError


DEFAULT_NAME = "default"
# The name the old config and requests used. It reads as the same thing.
LEGACY_DEFAULT_IDS = ("local-hayamimi",)


class DefaultLocal(ASRBackend):
    """The default: VAD-segment locally and decode with the bundled Whisper."""

    name = DEFAULT_NAME

    def transcribe(self, samples, lang, on_progress=None, speakers=False,
                   should_stop=None, refine=True):
        import transcribe_vod as vod
        return vod.transcribe(samples, lang, on_progress=on_progress,
                              speakers=speakers, should_stop=should_stop,
                              refine=refine)


LocalHayamimi = DefaultLocal       # The old name. Tests and scripts may call it.


class TranscribeCpp(ASRBackend):
    """Decodes segments with transcribe.cpp's GGUF models.

    Segmentation and timestamp computation are the VAD's job exactly as on the
    local path; only the decode is swapped.
    """

    name = "tcpp"

    def __init__(self, spec: dict):
        self.spec = spec

    def transcribe(self, samples, lang, on_progress=None, speakers=False,
                   should_stop=None, refine=True):
        import transcribe_vod as vod
        from tcpp_asr import build_live_asr
        engine = build_live_asr(self.spec, lang, threads=4)
        return vod.transcribe(samples, lang, on_progress=on_progress,
                              speakers=speakers, asr=engine,
                              should_stop=should_stop, refine=refine)


class OpenAICompatibleASR(ASRBackend):
    """POST windows of audio to /v1/audio/transcriptions (VOD).

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

    def transcribe(self, samples, lang, on_progress=None, speakers=False,
                   should_stop=None, refine=True):
        """`speakers` and `refine` are taken and ignored.

        This path sends a whole window to the remote and gets the segment
        timestamps back with it, so it is already doing what refinement sets out
        to do (join the short cuts back together and decode them). Speaker tags
        are CAM++ running on this machine, so the remote path has none.
        """
        spans = self._cut_points(samples)
        cues: list[dict] = []
        total = len(samples)
        for start, end in spans:
            # One window is tens of seconds of audio, so checking between them is enough.
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
    """For live: sends one utterance slice the VAD cut out to the remote as it comes.

    The same surface as `tcpp_asr.TranscribeCppASR` -- all `run_stream` and
    `Refiner` call is the one `transcribe(samples, sample_rate, speech_s=…,
    live=…)` plus `forced_lang` and `label`, so when those two match the session
    does not know which one went in. Swapping is `tcpp_asr.LiveASR`'s job.

    Failures are raised as exceptions. `_drain` drops just that slice and moves
    on for up to five in a row, and after that it lets the session go -- the same
    rule as the local engine.
    """

    name = "openai-asr"

    def __init__(self, spec: dict, lang: str | None):
        if not spec.get("base_url") or not spec.get("model"):
            raise ValueError("An OpenAI-compatible transcription engine needs base_url and model")
        self.base_url = spec["base_url"].rstrip("/")
        self.model = spec["model"]
        self.api_key = spec.get("api_key", "")
        # One slice is 12 seconds at the longest. With no answer within 30 seconds
        # that slice is dropped.
        self.timeout = float(spec.get("timeout") or 30.0)
        self.forced_lang = lang or ""
        self.min_switch_s = 0.0
        host = urllib.parse.urlsplit(self.base_url).netloc or self.base_url
        self.label = spec.get("label") or f"{self.model} @ {host}"
        self.device = "remote"
        self.threads = 0
        self.hallucinations = 0

    # --- The RoutedASR surface ----------------------------------------------
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

    # It cannot produce segment timestamps. This surface sends a whole utterance
    # slice and gets only text back -- VOD refinement (the re-split) does not hold.
    supports_segments = False

    def transcribe(self, samples: np.ndarray, sample_rate: int,
                   known_lang: str | None = None, speech_s: float | None = None,
                   live: bool = True, segments: bool = False) -> dict:
        """`segments` (the segment timestamps) is taken and ignored.

        This remote surface sends a whole utterance slice and gets only text
        back. The caller already has to handle "asked for them and they did not
        come" -- a remote server may not give verbose_json -- so turning back
        empty-handed is better than raising an exception here.
        """
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"Only 16kHz is supported (got {sample_rate})")
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
            print(f"[hallucination guard] {self.label}: {text[:50]}", flush=True)
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


