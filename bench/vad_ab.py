"""Put Silero VAD v4 (the k2 re-export, 643KB) and v5 (2.3MB) on the same sample
and compare how each splits it into spans.

    .venv/bin/python bench/vad_ab.py [data/*.wav ...]

What is measured: the number of spans, their mean and median length, the fraction of spans
that hit the ceiling (max_speech), the total seconds judged to be speech, and the time
taken. There are no ground-truth labels, so this is not about "which one is right" but
about "how differently they cut" -- a low fraction hitting the ceiling with spans close to
sentence length is what whisper is comfortable with. If v5 is absent it is fetched from the
sherpa-onnx releases (2.3MB).
"""
from __future__ import annotations
import os, sys, time, urllib.request, wave, statistics as st
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stream

V5 = "silero_vad_v5.onnx"
URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad_v5.onnx"
PROFILES = {"talk": (0.35, 12.0), "broadcast": (0.30, 4.0), "collab": (0.25, 3.0)}


def ensure_v5():
    p = os.path.join(stream.model_dir(), V5)
    if not os.path.exists(p):
        print(f"  {V5} 받는 중…", file=sys.stderr)
        urllib.request.urlretrieve(URL, p + ".part"); os.replace(p + ".part", p)
    return p


def segments(samples, model_file, min_sil, max_sp):
    vad = stream.build_vad(min_silence=min_sil, max_speech=max_sp, model_file=model_file)
    out = []
    for i in range(0, len(samples), 1600):
        vad.accept_waveform(samples[i:i + 1600])
        while not vad.empty():
            seg = vad.front; out.append(len(seg.samples) / 16000); vad.pop()
    vad.flush()
    while not vad.empty():
        seg = vad.front; out.append(len(seg.samples) / 16000); vad.pop()
    return out


def main(files):
    ensure_v5()
    for f in files:
        with wave.open(f) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        dur = len(pcm) / 16000
        print(f"\n== {os.path.basename(f)}  {dur/60:.0f}분")
        print(f"  {'프로필':<9} {'모델':<4} {'구간':>5} {'평균':>6} {'중앙':>6} {'상한걸림':>8} {'말(초)':>7} {'소요':>6}")
        for name, (ms, mx) in PROFILES.items():
            for tag, mf in (("v4", "silero_vad.onnx"), ("v5", V5)):
                t0 = time.time(); segs = segments(pcm, mf, ms, mx); el = time.time() - t0
                if not segs: print(f"  {name:<9} {tag:<4} 구간 없음"); continue
                capped = sum(1 for s in segs if s >= mx - 0.05) / len(segs)
                print(f"  {name:<9} {tag:<4} {len(segs):>5} {st.mean(segs):>6.2f} {st.median(segs):>6.2f}"
                      f" {capped*100:>7.0f}% {sum(segs):>7.0f} {el:>5.1f}s")


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(os.path.join("data", n) for n in os.listdir("data") if n.endswith(".wav")))
