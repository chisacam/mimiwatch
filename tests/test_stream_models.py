"""The refiner thread's lifetime, and model sharing."""
import gc
import threading
import weakref

import models
import stream


class FakeASR:
    forced_lang = "ja"

    def transcribe(self, *a, **k):
        return {"text": "", "lang": "ja"}


def test_refiner_close_releases_the_model():
    asr = FakeASR()
    r = stream.Refiner(asr, stream.AudioHistory(), object())
    ref = weakref.ref(asr)
    before = threading.active_count()
    r.close()
    r.close()                                    # Calling it twice is fine
    del asr, r
    gc.collect()
    assert ref() is None                         # The thread used to hold on and keep it alive
    assert threading.active_count() == before - 1


def test_refiner_close_waits_for_pending_work():
    seen = []

    class Sink:
        def refine(self, text, lang, speaker):
            seen.append(text)

    class ASR(FakeASR):
        def transcribe(self, buf, sr, **k):
            return {"text": "refined text", "lang": "ja"}

    hist = stream.AudioHistory()
    import numpy as np
    hist.push(np.zeros(16000 * 3, dtype=np.float32))
    r = stream.Refiner(ASR(), hist, Sink())
    r.add_span(0, 16000 * 2, "fast", "")
    r.maybe_refine(16000 * 3, force=True)
    r.close()
    assert seen == ["refined text"]


def test_models_shared_builds_once():
    made = []

    def factory():
        made.append(1)
        return object()
    a = models.shared(("k", 1), factory)
    b = models.shared(("k", 1), factory)
    c = models.shared(("k", 2), factory)
    assert a is b and a is not c and made == [1, 1]
    assert any("k" in r for r in models.resident())
    models.clear()
    assert models.resident() == []


def test_models_shared_under_contention_builds_once():
    import time
    made = []

    def slow():
        time.sleep(0.05)
        made.append(1)
        return object()
    got = []
    ts = [threading.Thread(target=lambda: got.append(models.shared("slow", slow))) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(made) == 1 and all(g is got[0] for g in got)


def test_run_stream_flushes_and_refines_at_end_of_audio():
    import numpy as np

    class VAD:
        def __init__(self):
            self.flushed = 0
            self.speech = False

        def accept_waveform(self, chunk):
            pass

        def flush(self):
            self.flushed += 1

        def empty(self):
            return True

        def is_speech_detected(self):
            return self.speech

    class Refiner:
        def __init__(self):
            self.forced = []

        def maybe_refine(self, now, force=False):
            self.forced.append(force)

        def add_span(self, *a):
            pass

    vad, ref = VAD(), Refiner()
    chunks = [np.zeros(1600, dtype=np.float32)] * 3
    stream.run_stream(chunks, vad, FakeASR(), None, stream.AudioHistory(), ref)
    assert vad.flushed == 1
    assert ref.forced[-1] is True                # When the audio ends, the last group is refined too
