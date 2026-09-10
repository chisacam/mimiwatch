"""The VOD refinement pass: grouping, re-splitting, backing out, inheriting speakers.

No model is loaded. A fake stands in for the transcriber and only the **rules**
are examined -- where a group is cut, how segment times are carried over into
subtitle lines, and whether the finals are kept where nothing improves. The
quality itself is measured in measurements/RESULTS.md sections 48-51.
"""
import numpy as np
import pytest

import stream
import transcribe_vod as vod

SR = vod.SAMPLE_RATE


def span(a: float, b: float) -> tuple[int, int]:
    return int(a * SR), int(b * SR)


class FakeASR:
    """Remembers the length of every chunk it was called with.

    The final pass (`segments=False`) and the refinement pass (`segments=True`)
    are called from different places, so their answers are kept apart -- to see
    whether refinement really swapped the final out, the two answers have to
    differ. Giving `refined` pins the text of the refined answer; leaving it out
    makes it the segment texts joined together.
    """

    def __init__(self, segments=None, text="확정", refined=None,
                 supports_segments=True, label="fake"):
        self.segments = segments if segments is not None else []
        self.text = text
        self.refined = refined
        self.supports_segments = supports_segments
        self.label = label
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
    # 2 s of silence (GROUP_GAP_S) ends a group.
    assert vod.refine_groups([span(0, 1), span(1.5, 2.5), span(10, 11)]) == [[0, 1], [2]]
    # Even unbroken speech is cut at 25 s (GROUP_MAX_S).
    long = [span(i, i + 1) for i in range(40)]
    groups = vod.refine_groups(long)
    assert len(groups) > 1
    for g in groups:
        first, last = long[g[0]][0], long[g[-1]][1]
        assert (last - first) / SR <= stream.GROUP_MAX_S


def test_refine_splits_the_group_back_into_lines_and_keeps_the_speaker():
    """The re-split is the heart of this pass -- one line per group lost at the tail in section 49."""
    samples = np.zeros(20 * SR, dtype=np.float32)
    cues = [{"start": 2.0, "end": 4.0, "lang": "ja", "text": "가", "speaker": "S1"},
            {"start": 4.5, "end": 6.0, "lang": "ja", "text": "나", "speaker": "S2"}]
    spans = [span(2, 4), span(4.5, 6)]
    # The chunk bites 1 s of lead-in and so starts at the 1 s mark -- segment times are on that base.
    asr = FakeASR([{"start": 1.0, "end": 3.0, "text": "가나다"},
                   {"start": 3.5, "end": 5.0, "text": "라마바"}], text="가 나")

    out = vod.refine_cues(samples, cues, spans, asr)

    assert [c["text"] for c in out] == ["가나다", "라마바"]
    assert [c["start"] for c in out] == [2.0, 4.5]
    assert [c["end"] for c in out] == [4.0, 6.0]
    # The speaker is inherited from the final line that overlaps in time the most.
    assert [c["speaker"] for c in out] == ["S1", "S2"]
    assert out[0]["lang"] == "ja"
    # One group means one decode, of 5 s with the 1 s of lead-in bitten in.
    assert asr.calls == pytest.approx([5.0])


def test_a_swallowed_refine_keeps_the_fast_lines():
    """When the re-decode comes back far shorter than the finals, the whole group is thrown away.

    Swapping perfectly good subtitles for a decode that swallowed the speech is
    the worst outcome there is.
    """
    samples = np.zeros(10 * SR, dtype=np.float32)
    cues = [{"start": 1.0, "end": 3.0, "lang": "ja", "text": "긴 문장이 하나 있었습니다"}]
    asr = FakeASR([{"start": 0.5, "end": 1.5, "text": "음"}])

    out = vod.refine_cues(samples, cues, [span(1, 3)], asr)
    assert out == cues


def test_an_engine_that_promises_timestamps_but_sends_none_keeps_the_fast_lines():
    """We asked and got back empty hands -- a remote server may not give verbose_json."""
    samples = np.zeros(10 * SR, dtype=np.float32)
    cues = [{"start": 1.0, "end": 3.0, "lang": "ja", "text": "그대로"}]
    asr = FakeASR([], refined="아주 길게 다시 받아 적었습니다만 시각이 없습니다")

    out = vod.refine_cues(samples, cues, [span(1, 3)], asr)
    assert out == cues


def test_a_model_without_timestamps_lands_the_text_on_the_vad_boundaries():
    """A model that cannot produce segment times re-decodes the group anyway.

    Until this the pass was skipped for these models (the light defaults), and
    the skip lived only in a server log line. Lumping a group into one line is
    the side that lost in section 49, but so is a refinement that never
    happens: the soft side keeps the VAD times and swaps the text.
    """
    samples = np.zeros(10 * SR, dtype=np.float32)
    cues = [{"start": 1.0, "end": 3.0, "lang": "ja", "text": "가나다", "speaker": "S1"},
            {"start": 4.0, "end": 5.0, "lang": "ko", "text": "라마", "speaker": "S2"}]
    # Same length as the finals joined, so the rollback cannot catch it.
    asr = FakeASR(text="가가가라라", supports_segments=False)

    out = vod.refine_cues(samples, cues, [span(1, 3), span(4, 5)], asr)

    assert asr.calls == pytest.approx([5.0])   # One group, one decode (with the lead-in)
    # The VAD boundaries stay as they were; only the text is swapped.
    assert [c["start"] for c in out] == [1.0, 4.0]
    assert [c["end"] for c in out] == [3.0, 5.0]
    assert [c["speaker"] for c in out] == ["S1", "S2"]   # The labels ride along
    assert [c["lang"] for c in out] == ["ja", "ja"]     # The re-decode's lang, group-wide
    # The piece each line takes divides the text by how long its final was.
    assert out[0]["text"] + out[1]["text"] == "가가가라라"
    assert len(out[0]["text"]) > len(out[1]["text"])


class FakeVAD:
    """A VAD that hands back the spans it was given. It is here so silero is never loaded."""

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
    """With `refine=False` there is no refinement decode at all.

    Re-decoding a whole group is 30-50% of the transcription time (section 49),
    so turning it off has to mean "the work is not done", not "the result comes
    out the same".
    """
    spans = [span(1, 3), span(3.5, 5)]
    samples = np.zeros(6 * SR, dtype=np.float32)
    monkeypatch.setattr(vod, "build_vad", lambda **kw: FakeVAD(spans))

    off = FakeASR([{"start": 0.0, "end": 1.0, "text": "정제된 문장"}])
    cues = vod.transcribe(samples, "ja", asr=off, refine=False)
    assert [c["text"] for c in cues] == ["확정", "확정"]
    assert off.calls == pytest.approx([2.0, 1.5])       # Two spans, and that is all

    on = FakeASR([{"start": 1.0, "end": 2.0, "text": "정제된 문장"}])
    cues = vod.transcribe(samples, "ja", asr=on, refine=True)
    assert [c["text"] for c in cues] == ["정제된 문장"]   # One group re-split into one line
    assert on.calls == pytest.approx([2.0, 1.5, 5.0])   # Two spans + one group (with the 1 s of lead-in)


def _progress(monkeypatch, asr, seconds=60):
    """Pull out the progress values alone. The callback fires once every 30 s, so the sample has to be long."""
    monkeypatch.setattr(vod, "build_vad", lambda **kw: FakeVAD([span(1, 3)]))
    seen = []
    vod.transcribe(np.zeros(seconds * SR, dtype=np.float32), "ja", asr=asr,
                   on_progress=seen.append, refine=True)
    return seen


def test_refining_reserves_the_tail_of_the_progress_bar(monkeypatch):
    """A fast pass that reaches 100% and then keeps running reads as having hung."""
    asr = FakeASR([{"start": 1.0, "end": 2.0, "text": "정제된 문장"}])
    seen = _progress(monkeypatch, asr)
    assert seen == sorted(seen)                       # It never goes backwards
    assert seen[-1] == pytest.approx(1.0)             # It fills up once refinement is done
    # The 30 s mark (halfway) has to land inside the fast pass's share.
    assert seen[1] == pytest.approx(0.5 * (1.0 - vod.REFINE_SHARE))


def test_the_soft_refine_also_takes_the_reserved_share_of_the_progress_bar(monkeypatch):
    """Whether the reserved tail is spent hangs on whether the pass runs at all.

    A model without timestamps is not an engine that skips the pass, it is one
    that takes a different cut of it, so the bar has to pay the same share.
    """
    asr = FakeASR(text="정제된 문장", supports_segments=False)
    seen = _progress(monkeypatch, asr)
    assert seen[1] == pytest.approx(0.5 * (1.0 - vod.REFINE_SHARE))
    assert asr.calls == pytest.approx([2.0, 3.0])    # One final pass + one group decode
