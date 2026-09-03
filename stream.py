"""The loop that cuts audio into utterances, writes them down, and decodes a
finished group again.

The structure comes from `realtime_transcribe` in hayamimi (MIT, oboroge0).
That one deals with sound coming in without knowing the language, though, so it
carries language identification, per-language model routing, splitting when the
language changes inside a group, and re-judging the language after a re-decode.
This project fixes one language per session and handles it with a single
multilingual model (whisper-large-v3-turbo), so half of that is dead code. It
was cleared out while porting.

Three things were kept.

- **Lead-in audio**: the cut is passed on from a little before the utterance
  start the VAD found. It stops the head of the speech from being cut away.
- **Two-pass refinement**: separately from the finals cut short and sent out
  at once, an utterance group that is over is joined up and decoded again. The
  context gets longer and the result gets better.
- **Order guaranteed**: refinement is handled by one worker thread in the order
  things went in. Starting a thread per call makes the start order and the run
  order differ, and the subtitles come out jumbled.
"""
from __future__ import annotations

import importlib.util
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np
import sherpa_onnx

import paths

SAMPLE_RATE = 16000
WINDOW_SIZE = 512      # how many samples the VAD looks at at once (about 32ms at 16kHz)

# The cut is passed on from this much before the utterance start the VAD found.
# Losing a single consonant changes the whole first word, so leaving some room
# is the better way.
PREROLL_S = 1.0

GROUP_GAP_S = 2.0      # this much silence is taken as an utterance group being over
GROUP_MAX_S = 25.0     # cut here even if the talking never pauses (the audio-keeping limit)

# If a re-decode comes out this much shorter than the finals joined together it
# is not trusted. A re-decode polishes the content, it does not lose it, so a
# large drop means something went wrong.
REFINE_MIN_KEEP = 0.7


def model_dir() -> str:
    """Where the models are kept. Changeable with `MIMIWATCH_MODEL_DIR`.

    The rules are in `paths.py` (`%LOCALAPPDATA%` on Windows, `~/.local/share`
    elsewhere). This used to work it out itself, but the config and the storage
    have to come out of the same root in a bundle (PyInstaller), so it was
    gathered into one place. This name has many callers, so it stays.
    """
    return paths.model_dir()


class Cancelled(RuntimeError):
    """The user stopped the job.

    Transcription is the longest stage on an hour-long video. Cancellation used
    to be checked only between the stages, so once transcription had started it
    could not be stopped until it finished -- picking a heavy model by mistake
    on a low-spec machine left nothing to do but wait. This exception is the way
    out from inside the decoding loop.
    """


_YTDLP: list[str] | None = None
_FFMPEG: str | None = None

# How long (in seconds) one yt-dlp call is allowed. When YouTube's side stalls
# yt-dlp stalls with it, and without an upper bound that request thread (or
# session) waits forever. Resolving an address usually takes a few seconds.
YTDLP_TIMEOUT_S = 90.0


def ytdlp_cmd() -> list[str]:
    """The command that calls yt-dlp.

    **What is installed in the virtualenv comes first.** yt-dlp is a Python
    package and the install script puts the latest one in there. That hides an
    old version on the system -- YouTube changes its extraction paths often, so
    a version a few months old fails to get the format list at all (issue #1).

    It is called as `python -m yt_dlp`. No path has to be guessed, and it does
    not matter where the script shim was put or under what name.

    Without it, it falls back to `yt-dlp` on PATH. That is what an older
    install looks like.

    **A standalone executable fetched into the tools directory comes before all
    of them.** Shipping as a bundle (PyInstaller) pins the version of the
    yt-dlp inside, so a few months later it is stale -- the very problem that
    keeping it in the virtualenv solved comes back in the bundle. The
    standalone executable yt-dlp ships updates itself with `-U`, so fetching it
    in "Engine management › Models and tools" means the latest is used without
    building the bundle again.

    Inside a bundle there is no Python to call `python -m yt_dlp` with.
    Starting the executable itself again with `--ytdlp` makes `app.py` see that
    argument and hand it over to yt_dlp's main. The reason for keeping it a
    child process is unchanged -- it has to be killable under a time limit
    (when YouTube stalls, it stalls with it).
    """
    global _YTDLP
    if _YTDLP is None:
        standalone = paths.tool("yt-dlp")
        if standalone:
            _YTDLP = [standalone]
        # find_spec does not run the module. Importing it takes close to a
        # second, and this function is called often.
        elif importlib.util.find_spec("yt_dlp") is not None:
            _YTDLP = ([sys.executable, "--ytdlp"] if paths.frozen()
                      else [sys.executable, "-m", "yt_dlp"])
        else:
            _YTDLP = ["yt-dlp"]
        _YTDLP += _js_runtime_args()
        _YTDLP += _cookie_args()
    return list(_YTDLP)


def deno_path() -> str | None:
    """deno, to solve YouTube's JS challenge. Tools directory → the usual
    places → PATH."""
    return paths.which("deno")


def _js_runtime_args() -> list[str]:
    """Tell yt-dlp where the JS runtime is.

    Since 2025.11 YouTube extraction is only whole with an external JS runtime
    (deno). On macOS and Linux yt-dlp searches **PATH only**, so a bundle
    started from Finder (where PATH is short) cannot find a Homebrew deno and
    knows nothing of one fetched through "Models and tools" -- hence handing
    the path over directly. `--remote-components ejs:github` is the insurance
    that has the solver script (yt-dlp-ejs) fetched from GitHub when it is
    missing or its version does not line up. It is off by default, and the
    formats just quietly disappear.
    """
    deno = deno_path()
    if not deno:
        return []
    return ["--js-runtimes", f"deno:{deno}", "--remote-components", "ejs:github"]


def ytdlp_args(*opts: str, url: str) -> list[str]:
    """One yt-dlp call line. The address always goes after `--`.

    Pasting something that starts with `-` into the address box makes it read
    as an option -- with `--version` a version number comes out and that is
    that, but with `--exec` a command runs. A pasted value sometimes comes from
    somewhere the user does not control (a chat, a post), so all five call
    sites are made to pass through here. `--no-playlist` goes on here too: when
    `-j` printed several lines for an address with a playlist attached, the
    caller's JSON parsing fell over whole.
    """
    return ytdlp_cmd() + ["--no-warnings", "--no-playlist", *opts, "--", url]


def _usable_stderr():
    """A standard error that can be handed down to a child. `DEVNULL` if it is
    not sound.

    `fileno()` can answer with a number while the handle behind it is closed,
    so it is knocked on once with `fstat`. A Python that came up without a
    window has `sys.stderr` as None outright.
    """
    try:
        fd = sys.stderr.fileno()
        os.fstat(fd)
        return fd
    except Exception:
        return subprocess.DEVNULL


def child_io(*, stderr: bool = True) -> dict:
    """The standard I/O to hand to a child process (ffmpeg, yt-dlp).

    **Inherited handles are not relied on.** Unless stdin/stderr are given
    separately, `Popen` duplicates the parent's (DuplicateHandle) to hand them
    down, and if that handle of the parent's is not valid it falls over with

        OSError: [WinError 6] 핸들이 잘못되었습니다

    before the child is even born. A Windows user on 0.3.1 died exactly this
    way in a live session's `_spawn_ffmpeg` -- a process that came up with an
    unsound standard error (a bundle started without a console, one started by
    the task scheduler or as a service, a shell called with its output
    redirected oddly). The yt-dlp call before it survived because
    `capture_output=True` was holding stdout and stderr on pipes, and the
    ffmpeg that had a pipe on stdout only was the one that got caught.

    stdin is always NUL. There is nothing to feed either ffmpeg or yt-dlp, and
    it also stops the child from taking the keystrokes when running in a
    terminal.

    stderr is handed down as it is when there is a sound one -- a line of
    ffmpeg's error has to be left in the console or in mimiwatch.log for the
    next report to be diagnosable. If it is not sound it is thrown away. At the
    call sites that catch stderr themselves (`capture_output`, `stderr=PIPE`)
    it is called with `stderr=False` -- it cannot be given twice.
    """
    kw = {"stdin": subprocess.DEVNULL}
    if stderr:
        kw["stderr"] = _usable_stderr()
    return kw


def ffmpeg_cmd() -> str:
    """The ffmpeg executable. In the order PATH → the usual places (Homebrew
    and such) → the tools directory.

    The name `"ffmpeg"` used to be handed to Popen as it is. That works in a
    terminal, but a bundle started from Finder has a short PATH and cannot find
    the Homebrew one, and someone who took only the bundle without the install
    script has no ffmpeg at all. In that case it is fetched in "Engine
    management › Models and tools" and put in the tools directory
    (modelhub.py).

    Without it, a FileNotFoundError carrying what to do about it is raised.
    This error is carried straight into the error box of a session or a job.
    """
    global _FFMPEG
    if _FFMPEG is None or not os.path.exists(_FFMPEG):
        found = paths.which("ffmpeg")
        if not found:
            raise FileNotFoundError(
                "ffmpeg가 없습니다. 「엔진 관리 › 모델·도구」에서 받거나 "
                "(macOS) brew install ffmpeg / (윈도우) winget install Gyan.FFmpeg 로 "
                "설치하십시오.")
        _FFMPEG = found
    return _FFMPEG


def reset_tool_cache():
    """Called when a tool has just been fetched. The two above remember the
    value they found once."""
    global _YTDLP, _FFMPEG
    _YTDLP = None
    _FFMPEG = None


def _cookie_args() -> list[str]:
    """Taking in a members-only stream needs a logged-in cookie.

    YouTube does not take an id-and-password login and OAuth no longer works
    either. Cookies are all there is.

      MIMIWATCH_YTDLP_COOKIES            a cookie file (Netscape format)
      MIMIWATCH_YTDLP_COOKIES_BROWSER    straight from a browser (chrome, firefox …)

    **The file is the recommended side.** YouTube keeps rotating the account
    cookies of an open tab, so reading straight from the browser profile in
    everyday use goes invalid before long. Log in in an incognito window,
    export, and close that window, and those cookies are not rotated (see the
    README).

    With both, the file wins. Given together, yt-dlp overwrites the incognito
    session's cookies with the everyday ones.
    """
    path = (os.environ.get("MIMIWATCH_YTDLP_COOKIES") or "").strip()
    if path:
        return ["--cookies", path]
    # Cookies handed over by the extension (POST /api/cookies/youtube). If the
    # environment variable is there, that side comes first. Deleting the file
    # ("Delete" on the screen) drops it from the next call on --
    # reset_tool_cache is called along with it.
    pushed = paths.cookies_path()
    if os.path.isfile(pushed):
        return ["--cookies", pushed]
    browser = (os.environ.get("MIMIWATCH_YTDLP_COOKIES_BROWSER") or "").strip()
    if browser:
        return ["--cookies-from-browser", browser]
    return []


def default_threads(device: str) -> int:
    """More threads are used when running on the CPU.

    On the GPU path 4 is plenty -- the GPU does the heavy work and the CPU only
    takes the ends. Running on the CPU makes those 4 everything, so the number
    goes up with the core count. Using every logical core often makes it worse
    instead, so it stops at half.

    It lives here so that transcription and translation use the same rule. Both
    already import stream, so neither gets tied to the other's runtime.
    """
    if (device or "").strip().lower() == "cpu":
        return max(4, min(8, (os.cpu_count() or 8) // 2))
    return 4


# Which Silero file to use. The default is the k2 re-export (the v4 line,
# 643KB). `silero_vad_v5.onnx` (2.3MB) can be fetched from the same place and
# bench/vad_ab.py measures the two against each other.
VAD_FILE = os.environ.get("MIMIWATCH_VAD_MODEL") or "silero_vad.onnx"
# The threshold on the probability of counting as speech. Silero's default is
# 0.5, but it is **lowered to 0.3.** On four music samples that have reference
# subtitles, 0.5 → 0.3 was the only gain past the error margin, overall error
# rate 64.4% → 59.0% (0.5 takes singing over an accompaniment as silence: on a
# 277 s song, 57 s of speech → 177 s), and on three conversational samples the
# seconds judged as speech, the empty stretches and the hallucination blocking
# were unchanged (RESULTS.md section 44). 0.2 got worse again (61.2%).
# `MIMIWATCH_VAD_THRESHOLD` puts it back.
VAD_THRESHOLD = float(os.environ.get("MIMIWATCH_VAD_THRESHOLD") or 0.3)


def build_vad(min_silence: float = 0.35,
              max_speech: float = 12.0,
              model_file: str | None = None,
              threshold: float | None = None) -> sherpa_onnx.VoiceActivityDetector:
    """The Silero VAD that cuts the utterances apart.

    min_silence is how quiet it has to be for an utterance to count as over,
    max_speech the length at which talking that never pauses is cut by force.
    The shorter the cut the sooner the subtitle comes out, but the shorter the
    context gets, and that loss is what the refinement pass undoes.
    """
    vad_model = os.path.join(model_dir(), model_file or VAD_FILE)
    if not os.path.exists(vad_model):
        raise FileNotFoundError(
            f"{os.path.basename(vad_model)}가 없습니다: {vad_model}\n"
            "「엔진 관리 › 모델·도구」에서 받거나 MIMIWATCH_MODEL_DIR을 확인하십시오.")
    cfg = sherpa_onnx.VadModelConfig(
        silero_vad=sherpa_onnx.SileroVadModelConfig(
            model=vad_model,
            threshold=VAD_THRESHOLD if threshold is None else threshold,
            min_silence_duration=min_silence,
            min_speech_duration=0.25,
            window_size=WINDOW_SIZE,
            max_speech_duration=max_speech,
        ),
        sample_rate=SAMPLE_RATE,
        num_threads=1,
    )
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)


class AudioHistory:
    """Holds the recent audio and tears off the lead-in region and the source
    audio for refinement."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, keep_s: float = 30.0):
        self.sr = sample_rate
        self.keep = int(keep_s * sample_rate)
        self.buf = np.zeros(0, dtype=np.float32)
        self.offset = 0          # which sample of the whole buf[0] is
        self.last_seg_end = 0    # so the lead-in does not intrude on the previous utterance

    def push(self, chunk: np.ndarray):
        self.buf = np.concatenate([self.buf, chunk])
        if len(self.buf) > self.keep:
            drop = len(self.buf) - self.keep
            self.buf = self.buf[drop:]
            self.offset += drop

    def with_preroll(self, seg_start: int, seg_samples: np.ndarray) -> np.ndarray:
        want = max(seg_start - int(PREROLL_S * self.sr),
                   self.last_seg_end, self.offset)
        pre = self.buf[want - self.offset:seg_start - self.offset]
        self.last_seg_end = seg_start + len(seg_samples)
        return seg_samples if len(pre) == 0 else np.concatenate([pre, seg_samples])

    def slice(self, start: int, end: int) -> np.ndarray:
        lo = max(start - int(PREROLL_S * self.sr), self.offset)
        return self.buf[lo - self.offset:end - self.offset].copy()


class Refiner:
    """Joins a finished utterance group up and decodes it again.

    The finals are 2~4 second pieces decoded one by one, so they have no
    context. Once a group is over, the source audio of that stretch is handed
    over again whole, to be written down knowing what came before and after.
    """

    def __init__(self, asr, history: AudioHistory, sink,
                 sample_rate: int = SAMPLE_RATE):
        self.asr = asr
        self.history = history
        self.sink = sink
        self.sr = sample_rate
        self.spans: list[tuple[int, int, str, str]] = []   # start, end, text, speaker
        self._last_refined = ""          # for the refine_prompt experiment: the last refined line
        # There is one worker thread. Starting a thread per call makes the
        # start() order and the actual run order differ, and a group closed by
        # silence and a group closed by force come out swapped. With one
        # consumer draining one queue, the order things went in is the order of
        # the audio in time, so that cannot happen.
        self._tasks: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            task = self._tasks.get()
            if task is None:                              # the end mark close() sent
                self._tasks.task_done()
                return
            try:
                task()
            except Exception as exc:                      # one group failing
                print(f"[refine] 실패: {exc}", flush=True)  # must not kill the session
            finally:
                self._tasks.task_done()

    def close(self, timeout: float = 10.0):
        """End the worker thread. The groups left over are handled through
        before it comes out.

        While this did not exist, `_loop` waited at `get()` forever, and since
        that thread held `self`, `self.asr` (one copy of the transcription
        model), `history` and `sink` were not reclaimed even after the session
        ended. A model piled up per session, in effect. It must be called where
        a session ends -- calling it twice is fine.
        """
        if not self._thread.is_alive():
            return
        self._tasks.put(None)
        self._thread.join(timeout)

    def add_span(self, seg_start: int, seg_end: int, text: str, speaker: str):
        self.spans.append((seg_start, seg_end, text, speaker))

    def maybe_refine(self, now_sample: int, force: bool = False):
        if not self.spans:
            return
        first_start, last_end = self.spans[0][0], self.spans[-1][1]
        due = (force
               or now_sample - last_end >= int(GROUP_GAP_S * self.sr)
               or last_end - first_start >= int(GROUP_MAX_S * self.sr))
        if not due:
            return

        buf = self.history.slice(first_start, last_end)
        speakers = [sp for _, _, _, sp in self.spans if sp]
        speaker = max(set(speakers), key=speakers.count) if speakers else ""
        fast_joined = " ".join(t for _, _, t, _ in self.spans if t.strip())
        self.spans = []
        if len(buf) < self.sr // 2:
            return

        def work():
            # Re-decoding a 25 second group takes 0.5~1 second. Running it on
            # the receiving path as it is would delay the next utterance's
            # final by that much, so it is handled here.
            # The last refined line can be handed over as a prompt to the
            # refinement pass only (`refine_prompt` in the engine settings). It
            # is for an experiment aiming at proper-noun consistency and is off
            # by default.
            kw = {}
            if getattr(self.asr, "refine_prompt", False) and self._last_refined:
                kw["prompt"] = self._last_refined[-200:]
            got = self.asr.transcribe(buf, self.sr,
                                      speech_s=len(buf) / self.sr,
                                      live=False, **kw)
            text = got["text"].strip()
            if len(text) < REFINE_MIN_KEEP * len(fast_joined):
                text = fast_joined
            if not text.strip():
                return
            # The language this decode worked out is used, not forced_lang.
            # Under "Auto-detect" forced_lang is an empty string, and carrying
            # that out on the subtitle leaves the translator not knowing the
            # source language, so it simply turns back.
            lang = got.get("lang") or self.asr.forced_lang
            tag = f"{speaker}|{lang}" if speaker else lang
            print(f"[refine/{tag}] {text}", flush=True)
            self._last_refined = text
            self.sink.refine(text, lang, speaker)

        self._tasks.put(work)


def run_stream(chunks, vad, asr, sink, history: AudioHistory,
               refiner: Refiner | None = None, speaker_labeler=None,
               sample_rate: int = SAMPLE_RATE):
    """Take audio pieces, cut them with the VAD, and write the cut utterances
    down.

    A chunk that is not an ndarray is the signal to "flush now". Instead of
    waiting for a silence that will never come because the stream broke off,
    the utterance in progress is made final.
    """
    audio_pos = 0.0
    # One failed decode is not reason to let go of the whole stream. But if the
    # device really has died there is no use in going on trying, so after a run
    # of failures it does give up then. The same thinking as translate.py's
    # breaker.
    fails = 0
    for chunk in chunks:
        if not isinstance(chunk, np.ndarray):
            vad.flush()
            fails = _drain(vad, asr, sink, history, refiner, speaker_labeler,
                           sample_rate, fails)
            if refiner is not None:
                refiner.maybe_refine(int(audio_pos * sample_rate), force=True)
            continue

        vad.accept_waveform(chunk)
        history.push(chunk)
        audio_pos += len(chunk) / sample_rate

        fails = _drain(vad, asr, sink, history, refiner, speaker_labeler,
                       sample_rate, fails)
        if refiner is not None and not vad.is_speech_detected():
            refiner.maybe_refine(int(audio_pos * sample_rate))

    # The sound is over (the stream ended, ffmpeg ended). The utterance left
    # hanging is made final and the last group is refined too. Without this the
    # last few lines are left as rough finals only -- the same thing the None
    # signal above does, done once more at the end.
    vad.flush()
    _drain(vad, asr, sink, history, refiner, speaker_labeler, sample_rate, fails)
    if refiner is not None:
        refiner.maybe_refine(int(audio_pos * sample_rate), force=True)


# This many decodes failing in a row is taken as the device having died and the
# session is let go. A single failure throws that piece away and moves on --
# losing one line on a live stream and losing the whole stream are different
# stories.
DECODE_FAIL_LIMIT = 5


def _drain(vad, asr, sink, history, refiner, speaker_labeler, sample_rate,
           fails: int = 0) -> int:
    while not vad.empty():
        seg = vad.front
        t0 = time.perf_counter()
        samples = np.asarray(seg.samples, dtype=np.float32)
        seg_start, seg_end = seg.start, seg.start + len(samples)
        raw_speech_s = len(samples) / sample_rate
        samples = history.with_preroll(seg_start, samples)
        vad.pop()

        try:
            result = asr.transcribe(samples, sample_rate, speech_s=raw_speech_s)
        except Exception as exc:
            # A GPU driver does fall over on a single piece sometimes. This
            # exception used to go out through run_stream and end the session
            # whole.
            fails += 1
            print(f"[전사 실패 {fails}/{DECODE_FAIL_LIMIT}] "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if fails >= DECODE_FAIL_LIMIT:
                raise
            continue
        fails = 0
        latency_ms = (time.perf_counter() - t0) * 1000
        text = result["text"].strip()
        if not text:
            continue          # a sound effect or noise: no subtitle, no speaker is left

        speaker = speaker_labeler.label(samples, sample_rate) if speaker_labeler else ""
        print(f"[{speaker + '|' if speaker else ''}{result['lang']}/"
              f"{result.get('tier', '?')}] {text}  "
              f"(seg={len(samples) / sample_rate:.1f}s, "
              f"decode={result['decode_ms']:.0f}ms, latency={latency_ms:.0f}ms)",
              flush=True)
        sink.final(text, result["lang"], speaker)
        if refiner is not None:
            refiner.add_span(seg_start, seg_end, text, speaker)
    return fails
