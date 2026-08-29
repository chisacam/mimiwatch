"""Whisper 해독 손잡이 A/B: 기본값 vs 일본어 강화 임계값(WhisperJAV 관행).

    .venv/bin/python bench/whisper_ab.py data/MPSTWzF2ZKU.wav 600 1200

VAD(broadcast 프로필)로 자른 구간마다 두 설정으로 해독해 비교합니다. 정답 자막이 없으므로
재는 것은 (1) 빈 결과로 떨어진 구간 수 -- no_speech 판정, (2) 4-gram 다양도 검사에 걸린
반복 환각 수, (3) 글자 수와 해독 시간, (4) 두 설정의 결과가 다른 구간의 예시입니다.
"""
from __future__ import annotations
import os, sys, time, wave
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stream, tcpp_asr

CONFIGS = {
    "기본": {},
    "강화": {"no_speech_thold": 0.84, "logprob_thold": -1.3},
    "강화+prev_off": {"no_speech_thold": 0.84, "logprob_thold": -1.3, "condition_on_prev_tokens": False},
}


def main(path, start=0.0, end=None):
    with wave.open(path) as w:
        w.setpos(int(start * 16000))
        n = w.getnframes() - int(start * 16000) if end is None else int((end - start) * 16000)
        pcm = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    vad = stream.build_vad(min_silence=0.30, max_speech=4.0)
    hist = stream.AudioHistory()
    segs = []
    for i in range(0, len(pcm), 1600):
        chunk = pcm[i:i + 1600]; vad.accept_waveform(chunk); hist.push(chunk)
        while not vad.empty():
            s = vad.front; a = np.asarray(s.samples, dtype=np.float32)
            segs.append(hist.with_preroll(s.start, a)); vad.pop()
    print(f"구간 {len(segs)}개 ({start:.0f}~{(end or len(pcm)/16000):.0f}초)")
    results = {}
    for name, opts in CONFIGS.items():
        asr = tcpp_asr.TranscribeCppASR(tcpp_asr.default_whisper(), "ja", whisper=opts)
        t0 = time.time(); texts = []
        for seg in segs:
            texts.append(asr.transcribe(seg, 16000)["text"])
        el = time.time() - t0
        empty = sum(1 for t in texts if not t.strip())
        print(f"\n[{name}] 빈 구간 {empty}/{len(segs)} · 환각 차단 {asr.hallucinations} · "
              f"글자 {sum(len(t) for t in texts)} · 해독 {el:.1f}s")
        results[name] = texts
    base = results["기본"]
    for name, texts in results.items():
        if name == "기본": continue
        diff = [(i, base[i], texts[i]) for i in range(len(segs)) if base[i] != texts[i]]
        print(f"\n== 기본 vs {name}: 다른 구간 {len(diff)}개 (앞 8개)")
        for i, a, b in diff[:8]:
            print(f"  #{i}\n    기본: {a[:70]}\n    {name}: {b[:70]}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], float(a[1]) if len(a) > 1 else 0.0, float(a[2]) if len(a) > 2 else None)
