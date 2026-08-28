"""전사를 CPU로 돌렸을 때와 GPU로 돌렸을 때를 잽니다.

`device` 설정이 실제로 경로를 바꾸는지, 그리고 CPU 쪽이 쓸 만한 속도인지
확인하기 위한 것입니다. **여기서 나온 배수는 이 기계의 것**이고 다른
하드웨어로 옮겨지지 않습니다 -- 노트북 내장 그래픽과 개별 GPU는 사정이
전혀 다릅니다. 낮은 사양에서 고를 때는 그 기계에서 다시 재십시오.

    .venv/bin/python bench/asr_device.py [wav경로] [초]
"""
from __future__ import annotations

import os, sys, time, wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np                                          # noqa: E402
import stream, tcpp_asr                                     # noqa: E402

DEFAULT_WAV = "data/MPSTWzF2ZKU.wav"


def load(path: str, seconds: float, offset: float = 600.0) -> np.ndarray:
    with wave.open(path, "rb") as w:
        sr, n = w.getframerate(), w.getnchannels()
        w.setpos(int(offset * sr))
        raw = w.readframes(int(seconds * sr))
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n > 1:
        a = a.reshape(-1, n).mean(axis=1)
    return a, sr


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WAV
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    if not os.path.exists(path):
        sys.exit(f"오디오가 없습니다: {path}")
    pcm, sr = load(path, secs)
    print(f"표본: {path} {secs:.0f}초 · {sr}Hz · 논리 코어 {os.cpu_count()}\n")

    for device in ("auto", "cpu"):
        asr = tcpp_asr.build_live_asr({"backend": "tcpp", "device": device}, "ja")
        asr.transcribe(pcm[:sr], sr, speech_s=1.0, live=False)   # 예열
        t0 = time.time()
        out = asr.transcribe(pcm, sr, speech_s=secs, live=False)
        took = time.time() - t0
        print(f"  {device:<5} {asr.device:<6} {asr.threads}스레드  "
              f"{took:5.2f}초  {secs / took:5.1f}배속")
        print(f"        {out['text'][:70]}")
        del asr


if __name__ == "__main__":
    main()
