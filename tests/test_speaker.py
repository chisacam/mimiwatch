"""The speaker labeler: splitting, the threshold knob, solo mode, and the
graceful path when the model file is gone.

No model is loaded. A fake embedding extractor stands in for CAM++; it answers
from the *content* of the samples, so a test decides which voice a chunk is by
which numbers it feeds. What is examined is the rule -- which utterance lands
on which centroid, at which threshold, and what a missing onnx file costs the
job.
"""
import numpy as np
import pytest

import speaker_id as sid
import transcribe_vod as vod

SR = vod.SAMPLE_RATE


def span(a: float, b: float) -> tuple[int, int]:
    return int(a * SR), int(b * SR)


# Three voices, as unit vectors the fake hands back. A and C sit at 60 degrees
# (cosine 0.5) so a threshold between them sorts people one way or the other;
# B stands at right angles to both.
CENTER_A = np.array([1.0, 0.0, 0.0])
CENTER_B = np.array([0.0, 1.0, 0.0])
CENTER_C = np.array([0.5, 0.8660254, 0.0])


class _Stream:
    def __init__(self):
        self.chunks = []

    def accept_waveform(self, sr, samples):
        self.chunks.append(np.asarray(samples, dtype=np.float32))

    def input_finished(self):
        pass


class _FakeExtractor:
    """The embedding is the center the content asks for.

    A positive mean is voice A, a negative mean voice B, and silence (a zero
    mean) the last center -- the voices a test can point at by sign alone.
    """

    def __init__(self, centers):
        self.centers = [np.array(c, dtype=np.float32) for c in centers]

    def create_stream(self):
        return _Stream()

    def compute(self, s):
        m = float(np.mean(np.concatenate(s.chunks))) if s.chunks else 0.0
        if m > 0:
            return self.centers[0]
        if m < 0:
            return self.centers[1]
        return self.centers[-1]


@pytest.fixture
def extractor(monkeypatch, tmp_path):
    """Point the labeler at a fake model and install a fake extractor."""
    model = tmp_path / "campplus_sv.onnx"
    model.write_bytes(b"fake")
    monkeypatch.setattr(sid, "_model_path", lambda: str(model))
    monkeypatch.setattr(sid.sherpa_onnx, "SpeakerEmbeddingExtractorConfig",
                        lambda model, num_threads: object())

    def install(centers):
        fake = _FakeExtractor(centers)
        monkeypatch.setattr(sid.sherpa_onnx, "SpeakerEmbeddingExtractor",
                            lambda cfg: fake)
        return fake

    return install


def _voice(sign, n=8000):
    return np.full(n, sign, dtype=np.float32)


def test_two_voices_split_and_follow(extractor):
    extractor([CENTER_A, CENTER_B])
    lbl = sid.SpeakerLabeler(threshold=0.9)
    assert lbl.label(_voice(1.0), SR) == "S1"
    assert lbl.label(_voice(-1.0), SR) == "S2"
    # The voice comes back; the centroid is still its own.
    assert lbl.label(_voice(1.0), SR) == "S1"


def test_the_threshold_knob_decides_whether_a_third_is_a_new_person(extractor):
    centers = [CENTER_A, CENTER_C]   # 0.5 alike
    extractor(centers)
    # Low threshold: the third voice is taken as the first.
    low = sid.SpeakerLabeler(threshold=0.4)
    assert low.label(_voice(1.0), SR) == "S1"
    assert low.label(_voice(0.0), SR) == "S1"
    # High threshold: the same third voice makes a new speaker.
    high = sid.SpeakerLabeler(threshold=0.6)
    assert high.label(_voice(1.0), SR) == "S1"
    assert high.label(_voice(0.0), SR) == "S2"
    # ... and the first voice still finds its own centroid afterwards.
    assert high.label(_voice(1.0), SR) == "S1"


def test_solo_labels_everything_one(extractor):
    extractor([CENTER_A, CENTER_B, CENTER_C])
    lbl = sid.SpeakerLabeler(solo=True, threshold=0.9)
    for v in (_voice(1.0), _voice(-1.0), _voice(0.0), _voice(1.0), _voice(-1.0)):
        assert lbl.label(v, SR) == "S1"
    # One centroid, following the mean of everything it heard.
    assert len(lbl._centroids) == 1


def test_make_labeler_is_none_without_speakers(extractor):
    extractor([CENTER_A, CENTER_B])
    assert sid.make_labeler(False) is None


def test_make_labeler_carries_the_settings(extractor):
    extractor([CENTER_A, CENTER_B])
    lbl = sid.make_labeler(True, solo=True, threshold=0.8)
    assert lbl is not None
    assert lbl._solo is True
    assert lbl._threshold == 0.8
    # A None threshold keeps the default.
    plain = sid.make_labeler(True)
    assert plain is not None
    assert plain._threshold == sid.SIM_THRESHOLD


def test_make_labeler_without_the_model_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(sid, "_model_path",
                        lambda: str(tmp_path / "not-there.onnx"))
    assert sid.make_labeler(True) is None


class FakeVAD:
    """Hands back the spans it was given, so silero is never loaded."""

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


def test_the_vod_survives_a_missing_speaker_model(monkeypatch, tmp_path):
    """The job cost of a gone model is a transcript without chips, not a failure.

    The old path raised out of the constructor inside `transcribe` and the
    whole VOD died; the factory catches it and the cues simply carry no
    `speaker` key.
    """
    monkeypatch.setattr(sid, "_model_path",
                        lambda: str(tmp_path / "not-there.onnx"))
    monkeypatch.setattr(vod, "build_vad",
                        lambda **k: FakeVAD([span(0, 1.0)]))

    class _ASR:
        def transcribe(self, samples, sample_rate, speech_s=None, live=False,
                       segments=False):
            return {"text": "한 마디", "lang": "ko"}

    cues = vod.transcribe(np.zeros(SR * 2, dtype=np.float32), "ko",
                          speakers=True, asr=_ASR(), refine=False)
    assert len(cues) == 1
    assert "speaker" not in cues[0]


def test_the_labeler_rides_along_when_it_is_there(monkeypatch, tmp_path):
    """With the model present and the box ticked, the cues carry the chip."""
    model = tmp_path / "campplus_sv.onnx"
    model.write_bytes(b"fake")
    monkeypatch.setattr(sid, "_model_path", lambda: str(model))
    fake = _FakeExtractor([CENTER_A, CENTER_B])
    monkeypatch.setattr(sid.sherpa_onnx, "SpeakerEmbeddingExtractorConfig",
                        lambda model, num_threads: object())
    monkeypatch.setattr(sid.sherpa_onnx, "SpeakerEmbeddingExtractor",
                        lambda cfg: fake)
    monkeypatch.setattr(vod, "build_vad",
                        lambda **k: FakeVAD([span(0, 1.0)]))

    class _ASR:
        def transcribe(self, samples, sample_rate, speech_s=None, live=False,
                       segments=False):
            return {"text": "한 마디", "lang": "ko"}

    cues = vod.transcribe(np.zeros(SR * 2, dtype=np.float32), "ko",
                          speakers=True, asr=_ASR(), refine=False)
    assert len(cues) == 1
    assert cues[0]["speaker"] == "S1"
