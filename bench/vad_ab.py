"""Silero VAD v4(k2 재수출, 643KB) 와 v5(2.3MB) 를 같은 표본에 물려 구간 분할을 맞대어 봅니다.

    .venv/bin/python bench/vad_ab.py [data/*.wav ...]

재는 것: 구간 수, 평균·중앙 길이, 상한(max_speech)에 걸린 구간 비율, 말로 판정한 총 초,
그리고 소요 시간. 정답 라벨은 없으므로 "어느 쪽이 맞다"가 아니라 "얼마나 다르게 자르는가"
를 봅니다 -- 상한에 걸리는 비율이 낮고 구간이 문장 길이에 가까우면 whisper 가 편합니다.
v5 가 없으면 sherpa-onnx 릴리스에서 받습니다(2.3MB).
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
