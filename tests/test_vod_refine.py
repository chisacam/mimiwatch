"""녹화본 정제 패스: 무리 묶기, 되쪼개기, 되돌림, 화자 물려받기.

모델은 올리지 않습니다. 전사기 자리에 가짜를 두고 **규칙**만 봅니다 -- 무리를
어디서 끊는지, 구간 시각을 어떻게 자막 줄로 옮기는지, 좋아지지 않는 자리에서
확정본을 지키는지. 품질 자체는 measurements/RESULTS.md 48~51절이 재 두었습니다.
"""
import numpy as np
import pytest

import stream
import transcribe_vod as vod

SR = vod.SAMPLE_RATE


def span(a: float, b: float) -> tuple[int, int]:
    return int(a * SR), int(b * SR)


class FakeASR:
    """부른 조각의 길이를 기억합니다.

    확정 패스(`segments=False`)와 정제 패스(`segments=True`)는 부르는 자리가
    다르므로 답도 따로 둡니다 -- 정제가 확정본을 실제로 갈아 끼웠는지 보려면
    두 답이 달라야 합니다. `refined` 를 주면 정제 답의 글자를 못 박고,
    비우면 구간 글자를 이어 붙인 것이 됩니다.
    """

    def __init__(self, segments=None, text="확정", refined=None):
        self.segments = segments if segments is not None else []
        self.text = text
        self.refined = refined
        self.calls = []

    def transcribe(self, samples, sample_rate, speech_s=None, live=True, segments=False):
        self.calls.append(len(samples) / sample_rate)
        base = {"lang": "ja", "tier": "fake", "lid_ms": 0.0,
                "decode_ms": 0.0, "probe_ms": 0.0}
        if not segments:
            return {**base, "text": self.text}
        segs = [dict(x) for x in self.segments]
        text = (self.refined if self.refined is not None
                else " ".join(x["text"] for x in segs))
        return {**base, "text": text, "segments": segs}


def test_groups_close_on_silence_and_at_the_ceiling():
    # 2초(GROUP_GAP_S) 조용하면 무리가 끝납니다.
    assert vod.refine_groups([span(0, 1), span(1.5, 2.5), span(10, 11)]) == [[0, 1], [2]]
    # 쉬지 않고 말해도 25초(GROUP_MAX_S)에서 끊습니다.
    long = [span(i, i + 1) for i in range(40)]
    groups = vod.refine_groups(long)
    assert len(groups) > 1
    for g in groups:
        first, last = long[g[0]][0], long[g[-1]][1]
        assert (last - first) / SR <= stream.GROUP_MAX_S


def test_refine_splits_the_group_back_into_lines_and_keeps_the_speaker():
    """되쪼개기가 이 패스의 핵심입니다 -- 49절에서 무리 한 줄은 끝단이 졌습니다."""
    samples = np.zeros(20 * SR, dtype=np.float32)
    cues = [{"start": 2.0, "end": 4.0, "lang": "ja", "text": "가", "speaker": "S1"},
            {"start": 4.5, "end": 6.0, "lang": "ja", "text": "나", "speaker": "S2"}]
    spans = [span(2, 4), span(4.5, 6)]
    # 조각은 선행 1초를 물고 1초 자리에서 시작하므로, 구간 시각도 그 기준입니다.
    asr = FakeASR([{"start": 1.0, "end": 3.0, "text": "가나다"},
                   {"start": 3.5, "end": 5.0, "text": "라마바"}], text="가 나")

    out = vod.refine_cues(samples, cues, spans, asr)

    assert [c["text"] for c in out] == ["가나다", "라마바"]
    assert [c["start"] for c in out] == [2.0, 4.5]
    assert [c["end"] for c in out] == [4.0, 6.0]
    # 화자는 시간이 가장 많이 겹치는 확정 줄에서 물려받습니다.
    assert [c["speaker"] for c in out] == ["S1", "S2"]
    assert out[0]["lang"] == "ja"
    # 무리 하나이므로 해독은 한 번, 선행 1초를 문 5초짜리입니다.
    assert asr.calls == pytest.approx([5.0])


def test_a_swallowed_refine_keeps_the_fast_lines():
    """다시 해독한 것이 확정본보다 훨씬 짧으면 무리째 버립니다.

    말을 삼킨 해독으로 멀쩡한 자막을 바꿔치기하는 것이 가장 나쁩니다.
    """
    samples = np.zeros(10 * SR, dtype=np.float32)
    cues = [{"start": 1.0, "end": 3.0, "lang": "ja", "text": "긴 문장이 하나 있었습니다"}]
    asr = FakeASR([{"start": 0.5, "end": 1.5, "text": "음"}])

    out = vod.refine_cues(samples, cues, [span(1, 3)], asr)
    assert out == cues


def test_an_engine_without_timestamps_keeps_the_fast_lines():
    """구간 시각을 못 주는 전사기(원격)에서는 아무것도 바꾸지 않습니다."""
    samples = np.zeros(10 * SR, dtype=np.float32)
    cues = [{"start": 1.0, "end": 3.0, "lang": "ja", "text": "그대로"}]
    asr = FakeASR([], refined="아주 길게 다시 받아 적었습니다만 시각이 없습니다")

    out = vod.refine_cues(samples, cues, [span(1, 3)], asr)
    assert out == cues


class FakeVAD:
    """정해 둔 구간을 그대로 내놓는 VAD. silero 를 올리지 않으려고 둡니다."""

    class Seg:
        def __init__(self, start, samples):
            self.start, self.samples = start, samples

    def __init__(self, spans):
        self._pending = [self.Seg(lo, np.zeros(hi - lo, dtype=np.float32))
                         for lo, hi in spans]
        self._out = []

    def accept_waveform(self, chunk):
        pass

    def flush(self):
        self._out, self._pending = self._pending, []

    def empty(self):
        return not self._out

    @property
    def front(self):
        return self._out[0]

    def pop(self):
        self._out.pop(0)


def test_transcribe_runs_the_refine_pass_only_when_asked(monkeypatch):
    """`refine=False` 면 정제 해독이 아예 없습니다.

    무리째 다시 해독하는 것이 전사 시간의 30~50%(49절)이므로, 끄는 쪽은
    「결과가 같다」가 아니라 「그 일을 하지 않는다」여야 합니다.
    """
    spans = [span(1, 3), span(3.5, 5)]
    samples = np.zeros(6 * SR, dtype=np.float32)
    monkeypatch.setattr(vod, "build_vad", lambda **kw: FakeVAD(spans))

    off = FakeASR([{"start": 0.0, "end": 1.0, "text": "정제된 문장"}])
    cues = vod.transcribe(samples, "ja", asr=off, refine=False)
    assert [c["text"] for c in cues] == ["확정", "확정"]
    assert off.calls == pytest.approx([2.0, 1.5])       # 구간 둘, 그것뿐입니다

    on = FakeASR([{"start": 1.0, "end": 2.0, "text": "정제된 문장"}])
    cues = vod.transcribe(samples, "ja", asr=on, refine=True)
    assert [c["text"] for c in cues] == ["정제된 문장"]   # 무리 하나가 되쪼개져 한 줄
    assert on.calls == pytest.approx([2.0, 1.5, 5.0])   # 구간 둘 + 무리 하나(선행 1초)


def test_progress_never_goes_backwards_when_refining(monkeypatch):
    """빠른 패스가 100%에 닿고 한참 더 도는 것처럼 보이면 멈춘 것으로 읽힙니다."""
    spans = [span(1, 3)]
    samples = np.zeros(6 * SR, dtype=np.float32)
    monkeypatch.setattr(vod, "build_vad", lambda **kw: FakeVAD(spans))
    seen = []
    vod.transcribe(samples, "ja", asr=FakeASR([{"start": 1.0, "end": 2.0, "text": "가"}]),
                   on_progress=seen.append, refine=True)
    assert seen == sorted(seen) and max(seen) == pytest.approx(1.0)
    assert max(p for p in seen if p < 1.0) <= 1.0 - vod.REFINE_SHARE + 1e-9
