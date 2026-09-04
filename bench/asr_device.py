"""Measure transcription run on the CPU against transcription run on the GPU.

The point is to confirm that the `device` setting really does change the path,
and that the CPU side is fast enough to be usable. **The multiples printed here
belong to this machine** and do not carry over to other hardware -- integrated
laptop graphics and a discrete GPU are entirely different situations. When
choosing on a low-spec machine, measure again on that machine.

    .venv/bin/python bench/asr_device.py [wav path] [seconds] [model file] [language]

The third argument can name a different transcription model. Use it to see
whether the light one is usable on a machine that struggles with the default.

    .venv/bin/python bench/asr_device.py data/x.wav 20 SenseVoiceSmall-Q8_0.gguf
"""
from __future__ import annotations

import os, sys, time, wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np                                          # noqa: E402
import stream, tcpp_asr                                     # noqa: E402

DEFAULT_WAV = "data/MPSTWzF2ZKU.wav"


def load(path: str, seconds: float, offset: float = 600.0) -> np.ndarray:
    """Cut from somewhere around the middle.

    The first few seconds are a greeting or silence, which cannot tell the
    models apart. The file may be shorter than the default offset, though, so
    the offset is folded back to fit the length.
    """
    with wave.open(path, "rb") as w:
        sr, n = w.getframerate(), w.getnchannels()
        total = w.getnframes() / sr
        start = min(offset, max(0.0, total - seconds - 1.0))
        w.setpos(int(start * sr))
        raw = w.readframes(int(seconds * sr))
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n > 1:
        a = a.reshape(-1, n).mean(axis=1)
    return a, sr


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WAV
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    model = sys.argv[3] if len(sys.argv) > 3 else None
    lang = sys.argv[4] if len(sys.argv) > 4 else "ja"
    if not os.path.exists(path):
        sys.exit(f"no audio: {path}")
    pcm, sr = load(path, secs)
    print(f"sample: {path} {secs:.0f}s · {sr}Hz · {lang} · "
          f"{os.cpu_count()} logical cores\n")

    for device in ("auto", "cpu"):
        spec = {"backend": "tcpp", "device": device}
        if model:
            spec["model"] = model
        asr = tcpp_asr.build_live_asr(spec, lang)
        asr.transcribe(pcm[:sr], sr, speech_s=1.0, live=False)   # warm-up
        t0 = time.time()
        out = asr.transcribe(pcm, sr, speech_s=secs, live=False)
        took = time.time() - t0
        print(f"  {device:<5} {asr.device:<6} {asr.threads} threads  "
              f"{took:5.2f}s  {secs / took:5.1f}x")
        print(f"        {out['text'][:70]}")
        del asr


if __name__ == "__main__":
    main()
