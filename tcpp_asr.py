"""Adapter that fits transcribe.cpp's GGUF models into the hayamimi live path.

hayamimi's run_stream/Refiner takes a RoutedASR object and calls only a few of
its methods. Here a single model with a fixed language imitates that surface.
_identify_lang returns the fixed language as it is, so the Refiner's
language-re-judgement branch always closes as "no change", and
_get/_decode_full/_replace/ko_spacer are never called.
"""
from __future__ import annotations

import os
import sys
import threading

import numpy as np
from transcribe_cpp.errors import OutputTruncated, UnsupportedRequest

import models
import stream

# Runaway-repetition judgement: 4-gram diversity below this value counts as a
# hallucination. In measurements a healthy stretch was 0.92~0.98, and Fun-ASR's
# runaway stretch was 0.06.
NGRAM_N = 4
DIVERSITY_FLOOR = 0.35
MIN_CHARS_TO_JUDGE = 40


def ngram_diversity(text: str, n: int = NGRAM_N) -> float:
    """The ratio of distinct n-grams. Repeating the same phrase drives it towards 0."""
    s = "".join(text.split())
    if len(s) < n:
        return 1.0
    grams = [s[i:i + n] for i in range(len(s) - n + 1)]
    return len(set(grams)) / len(grams)


def looks_hallucinated(text: str) -> bool:
    t = text.strip()
    if len(t) < MIN_CHARS_TO_JUDGE:
        return False
    return ngram_diversity(t) < DIVERSITY_FLOOR


def resolve_device(want: str) -> str:
    """Decides which backend to use.

    `auto` picks the fastest one available -- if there is a GPU, it is the GPU.
    `cpu` runs on the CPU even when there is a GPU. Some machines are better off
    that way: integrated graphics share system memory with the CPU and have
    narrow bandwidth, so on a laptop with plenty of cores the CPU is faster, or
    at least does not get in the way of other work. It also keeps transcription
    and translation from fighting over the same small iGPU.

    One can also be named outright, like `vulkan`/`metal`/`cuda`/`rocm`. Naming
    one that is not there falls back to auto instead of refusing to start -- that
    is better than one config line stopping transcription altogether.
    """
    import transcribe_cpp as tc

    want = (want or "auto").strip().lower()
    if want in ("", "auto", "gpu"):
        # There is no 'gpu' policy. auto already picks the GPU first.
        return "auto"
    try:
        if tc.backend_available(want):
            return want
    except Exception:
        pass
    print(f"[asr] the '{want}' backend is not available, falling back to auto",
          file=sys.stderr)
    return "auto"


def _model_key(path: str, device: str):
    return ("tcpp", path, device)


def _shared_model(path: str, device: str):
    import transcribe_cpp as tc
    return models.shared(_model_key(path, device), lambda: tc.Model(path, backend=device))


class TranscribeCppASR:
    """A single-language adapter that goes in RoutedASR's place."""

    def __init__(self, model_path: str, lang: str | None, threads: int = 4,
                 label: str = "", device: str = "auto", whisper: dict | None = None,
                 refine_prompt: bool = False):
        self.device = resolve_device(device)
        # Whisper decode knobs. The config's (`asr_backends`) `whisper: {no_speech_thold,
        # logprob_thold, compression_ratio_thold, condition_on_prev_tokens, temperature,
        # temperature_inc}` is passed through as it is. Empty means the runtime defaults
        # (0.6 / -1.0 / 2.4 / false / 0.0+0.2). The grounds for changing a value are made by
        # measuring with bench/whisper_ab.py -- the defaults are left alone.
        self.whisper_opts = {k: v for k, v in (whisper or {}).items() if v is not None}
        # Whether to pass the previous refined line as initial_prompt to the refinement pass
        # only. It is not given to the fast pass -- conditioning on previous text is known to
        # grow repetition hallucinations (whisper.cpp #3744).
        self.refine_prompt = bool(refine_prompt)
        # Leaving the source language to automatic detection arrives here as
        # None. Left as it is, the lang transcribe puts out becomes None, and
        # that value rides a subtitle line all the way to store.save_cue and
        # hits the NOT NULL constraint. The whole session ended on the first
        # final line. Inside this code, "detection is left to the model" is
        # written one way only: as an empty string.
        self.forced_lang = lang or ""
        self.min_switch_s = 0.0
        # os.path.basename is used. Cutting on "/" alone makes the whole path
        # the engine name on screen for a Windows backslash path.
        self.label = label or os.path.basename(model_path)
        self.hallucinations = 0

        self.threads = threads
        # There is one copy of the model (the weights) per process (models.py).
        # The decode session is ours -- it holds state, so one per session is
        # right, and the cost of making one does not compare to loading the
        # weights.
        self._key = _model_key(model_path, self.device)
        self._model = _shared_model(model_path, self.device)
        self._session = self._model.session(n_threads=threads)
        self._check_language()
        print(f"[asr] {self.label} · {self.device} · {threads} threads",
              file=sys.stderr, flush=True)
        # The binding's session makes no promise about concurrent calls. On the
        # live path the fast pass and the refinement pass come in on different
        # threads, so they are serialised.
        self._lock = threading.Lock()
        # Whether this model can produce segment timestamps. VOD refinement is
        # nothing but re-splitting the re-decoded result by those timestamps
        # (section 49), so on a model that cannot produce them refinement is not
        # run at all -- asking raises UnsupportedRequest, and taking that once
        # per utterance group pays the decode cost and leaves the subtitles as
        # they were. The default light transcriber (SenseVoice Small) and
        # moonshine are `none`.
        self.supports_segments = self._model.capabilities.max_timestamp_kind in (
            "segment", "word", "token")

    def _check_language(self):
        """Asks at startup whether this model knows this language.

        Handing Japanese to an English-only model (moonshine and the like)
        raises UnsupportedRequest on every decode. Left alone, the session ends
        after barely a few subtitle lines, and the log piles up several lines of
        the same exception. It is a failure that cannot come out differently on
        a retry, so it is cut off here -- known the moment we start, not after
        taking 20 seconds of the stream.

        0.1 seconds of silence is enough. It costs about 50 milliseconds on moonshine.
        """
        self._probe_language(self._session, self.label)

    def _probe_language(self, session, label: str):
        if not self.forced_lang:
            return              # nothing to ask when detection is left to the model
        try:
            session.run(np.zeros(1600, dtype=np.float32),
                        language=self.forced_lang)
        except UnsupportedRequest as exc:
            raise RuntimeError(
                f"The {label} model does not support the '{self.forced_lang}' "
                f"language. Change the source language, or pick a different "
                f"transcription engine. ({exc})") from exc
        except Exception:
            # Other failures are not judged here. One slice of silence is no
            # grounds for a verdict on the whole model.
            pass

    # --- The attributes RoutedASR exposes -----------------------------------
    @property
    def punct(self):
        return None

    @property
    def ko_spacer(self):
        return None

    def resident_models(self) -> list[str]:
        return [self.label]

    def reset_session(self):
        pass

    # --- Language judgement: fixed, so nothing is judged --------------------
    def _identify_lang(self, samples: np.ndarray, sample_rate: int) -> str:
        return self.forced_lang

    def identify(self, samples: np.ndarray, sample_rate: int) -> str:
        return self.forced_lang

    def _replace(self, text: str) -> str:
        return text

    def partial(self, samples: np.ndarray, sample_rate: int,
                lang_hint: str | None = None) -> str:
        # When forced_lang is set, run_stream does not call partial.
        return ""

    # --- The transcription proper -------------------------------------------
    def _family(self, prompt: str | None):
        """Whisper-family knobs. None (the runtime defaults) if nothing is set."""
        opts = dict(self.whisper_opts)
        if prompt:
            opts["initial_prompt"] = prompt
        if not opts:
            return None
        import transcribe_cpp as tc
        try:
            return tc.WhisperRunOptions(**opts)
        except TypeError as exc:
            print(f"[asr] ignoring the whisper knobs: {exc}", file=sys.stderr, flush=True)
            return None

    def transcribe(self, samples: np.ndarray, sample_rate: int,
                   known_lang: str | None = None,
                   speech_s: float | None = None,
                   live: bool = True, prompt: str | None = None,
                   segments: bool = False) -> dict:
        """`segments` means "give me the segment timestamps (t0/t1) in the result too".

        It is used when a long slice is decoded in one go and then re-split into
        subtitle lines -- sending 25 seconds out as one line may read better as
        text but is unusable as a subtitle. It is off by default: asking for the
        timestamps takes the runtime down a different decode path, and there is
        no reason to pay that for the short slices of a live stream too.
        """
        import time

        if sample_rate != 16000:
            raise ValueError(f"Only 16kHz is supported (got {sample_rate})")

        pcm = np.ascontiguousarray(samples, dtype=np.float32)
        t0 = time.perf_counter()
        models.touch(self._key)          # says "in use" when idle unloading is on
        family = self._family(prompt)
        try:
            kw = {"family": family} if family is not None else {}
            if segments:
                kw["timestamps"] = "segment"
            with self._lock:
                result = self._session.run(pcm, language=self.forced_lang, **kw)
        except OutputTruncated:
            # Hitting the generation cap means 256 tokens came out of a slice a
            # few seconds long, and such a slice is not a person speaking but a
            # runaway repeating the same words. It is the same phenomenon the
            # diversity check below would catch, surfacing as an exception
            # first, so it is handled the same way.
            self.hallucinations += 1
            print(f"[hallucination guard] {self.label} hit the generation cap "
                  f"({len(samples) / sample_rate:.1f}s slice)", flush=True)
            return {"text": "", "lang": self.forced_lang, "tier": self.label,
                    "lid_ms": 0.0,
                    "decode_ms": (time.perf_counter() - t0) * 1000,
                    "probe_ms": 0.0, **({"segments": []} if segments else {})}
        decode_ms = (time.perf_counter() - t0) * 1000

        text = (result.text or "").strip()
        if looks_hallucinated(text):
            self.hallucinations += 1
            print(f"[hallucination guard] {self.label} diversity "
                  f"{ngram_diversity(text):.2f}: {text[:50]}", flush=True)
            text = ""

        # When the language is not nailed down, whatever the runtime worked out
        # is used as it is.
        #
        # Returning only self.forced_lang here was why a live session turned on
        # with "automatic detection" got not one line translated. The empty
        # string rides along on the subtitle, and LiveSession._translate simply
        # turns back when it does not know the source language -- the
        # transcription comes out fine and only the translation quietly goes
        # missing, which makes it hard to notice.
        got = {"text": text, "lang": self.forced_lang or (result.language or ""),
               "tier": self.label,
               "lid_ms": 0.0, "decode_ms": decode_ms, "probe_ms": 0.0}
        if segments:
            # A line wiped as a hallucination does not carry its segments along
            # either -- timestamps left on their own make the caller create
            # empty subtitles.
            got["segments"] = [] if not text else [
                {"start": sg.t0_ms / 1000.0, "end": sg.t1_ms / 1000.0,
                 "text": (sg.text or "").strip()}
                for sg in (result.segments or ()) if (sg.text or "").strip()]
        return got


# The per-language default arrangement.
#
# whisper-large-v3-turbo handles all of it on its own. It is a multilingual
# model, so the language can be picked and given to it, or left empty for it to
# detect by itself.
#
# The plan of attaching a specialist model per language was dropped on the
# measurements. In Japanese, Fun-ASR wrote down 7% more of a solo stream, but on
# a collab it ignored the fixed -l ja, spat out Vietnamese and Indonesian, and
# leaked even an internal token (!sil). In Korean, SenseVoice shortened
# `데이터독` to `데이터`, and it stayed that way even with the quantisation
# raised to F32. A model that holds up on everything beats one that only sees
# one kind of stream well. The grounds are in measurements/RESULTS.md
# sections 11-14.
#
# Only the file name is kept here. Where it lives is decided by
# `stream.model_dir()` -- this used to compute `~/.local/share/...` on its own,
# and that computation had no branch for Windows' `%LOCALAPPDATA%`. install.ps1
# fetches it there, so on Windows without `MIMIWATCH_MODEL_DIR` given separately
# the default transcriber's file was not found. There has to be one place that
# knows where the models are.
WHISPER_FILE = "whisper-large-v3-turbo-Q8_0.gguf"


def default_whisper() -> str:
    return os.path.join(stream.model_dir(), WHISPER_FILE)


def resolve_asr(spec: dict | None, lang: str | None) -> dict:
    """Pulls the model, device and threads actually to be used out of one config blob.

    It is kept apart because making a new one and swapping one in a running
    session (`LiveASR.swap`) have to use the same rules.
    """
    spec = spec or {}
    path = ((spec.get("models") or {}).get(lang or "") or spec.get("model")
            or default_whisper())
    # The config is allowed to carry just a file name. Demanding a full path
    # would mean the example cannot be used as it is, because the model location
    # differs on Windows and macOS.
    if not os.path.isabs(path) and not os.path.exists(path):
        path = os.path.join(stream.model_dir(), path)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"There is no transcription model: {path}\n"
            'Download it in "Engines › Models & Tools", or check MIMIWATCH_MODEL_DIR.')
    device = resolve_device(spec.get("device", "auto"))
    # A thread count written in the config wins. Without one, it is decided to
    # match where we are running.
    return {"path": path, "device": device,
            "threads": int(spec.get("threads") or stream.default_threads(device)),
            "label": os.path.basename(path).replace(".gguf", ""),
            "whisper": spec.get("whisper") or {},
            "refine_prompt": bool(spec.get("refine_prompt", False))}


def build_engine(spec: dict | None, lang: str | None, threads: int = 4):
    """One config blob into an actual recogniser.

    `backend: openai` means remote (a request per utterance slice), otherwise a
    local GGUF. Both put out the same surface (`transcribe(samples, sr, …)`,
    `forced_lang`, `label`).
    """
    spec = spec or {}
    if spec.get("backend") == "openai":
        from asr import OpenAIStreamASR
        return OpenAIStreamASR(spec, lang)
    r = resolve_asr(spec, lang)
    return TranscribeCppASR(r["path"], lang, threads=r["threads"],
                            label=r["label"], device=r["device"],
                            whisper=r["whisper"], refine_prompt=r["refine_prompt"])


class LiveASR:
    """The recogniser a session holds. Its inside (local GGUF or remote) can be swapped.

    **The object is not replaced, only its inside.** `run_stream` takes asr into
    a local variable and holds it, and `Refiner` keeps a reference of its own, so
    swapping the session's `_asr` for a new object leaves the running loop still
    using the old one. This shell is what those references point at, and when the
    inside changes the next decode goes to the new engine.

    Swapping used to mean restarting the whole session. That changed the session
    id, and because subtitles are stored by session id, the subtitle history up
    to then disappeared from the screen. Within one video the subtitles have to
    carry on.

    The new engine is built completely before the switch. If it does not support
    the language, or the model file is missing, or the remote config is empty, an
    exception is raised and what was in use stays -- losing the stream while
    trying to switch is the worst of all. A decode in flight finishes on the old
    engine, and everything after it is on the new one.
    """

    def __init__(self, spec: dict | None, lang: str | None, threads: int = 4):
        self.forced_lang = lang or ""
        self._inner = build_engine(spec, lang, threads)

    def swap(self, spec: dict | None) -> dict:
        new = build_engine(spec, self.forced_lang)
        self._inner = new
        print(f"[asr] swapped -> {new.label} · {new.device} · {new.threads} threads",
              file=sys.stderr, flush=True)
        return {"label": new.label, "device": new.device, "threads": new.threads}

    # The surface the session and the refiner see. All of it goes to the current inside.
    @property
    def label(self):
        return self._inner.label

    @property
    def device(self):
        return self._inner.device

    @property
    def threads(self):
        return self._inner.threads

    @property
    def hallucinations(self):
        return self._inner.hallucinations

    @property
    def min_switch_s(self):
        return self._inner.min_switch_s

    @property
    def punct(self):
        return self._inner.punct

    @property
    def ko_spacer(self):
        return self._inner.ko_spacer

    def resident_models(self):
        return self._inner.resident_models()

    def reset_session(self):
        return self._inner.reset_session()

    def _identify_lang(self, samples, sample_rate):
        return self._inner._identify_lang(samples, sample_rate)

    def identify(self, samples, sample_rate):
        return self._inner.identify(samples, sample_rate)

    def _replace(self, text):
        return self._inner._replace(text)

    def partial(self, samples, sample_rate, lang_hint=None):
        return self._inner.partial(samples, sample_rate, lang_hint)

    @property
    def refine_prompt(self):
        return getattr(self._inner, "refine_prompt", False)

    @property
    def supports_segments(self):
        return getattr(self._inner, "supports_segments", False)

    def transcribe(self, samples, sample_rate, known_lang=None, speech_s=None,
                   live=True, prompt=None, segments=False):
        kw = {"segments": True} if segments else {}
        if prompt is not None:
            kw["prompt"] = prompt
        return self._inner.transcribe(samples, sample_rate, known_lang=known_lang,
                                      speech_s=speech_s, live=live, **kw)


def build_live_asr(spec: dict | None, lang: str | None, threads: int = 4) -> LiveASR:
    """Builds the recogniser a session will use.

    An empty lang means the model detects by itself. Still, if the stream's
    language is known, naming it is better -- when detection wobbles, a whole
    sentence comes out in another language.
    """
    return LiveASR(spec, lang, threads)
