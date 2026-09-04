"""The live recogniser: the remote (OpenAI-compatible) adapter, swapping engines, idle eviction."""
import numpy as np

import asr
import models
import tcpp_asr


def _fake_post(calls, reply):
    def post(base_url, model, api_key, audio, lang, timeout):
        calls.append({"base_url": base_url, "model": model, "api_key": api_key,
                      "lang": lang, "timeout": timeout, "bytes": len(audio)})
        return reply
    return post


SPEC = {"id": "r", "backend": "openai", "base_url": "http://h:1/", "model": "whisper-1",
        "api_key": "k", "label": "원격"}


def test_remote_live_asr_posts_each_segment(monkeypatch):
    calls = []
    monkeypatch.setattr(asr, "post_transcription",
                        _fake_post(calls, {"text": " こんにちは ", "language": "japanese"}))
    a = asr.OpenAIStreamASR(SPEC, None)
    got = a.transcribe(np.zeros(16000, dtype=np.float32), 16000, speech_s=1.0)
    assert got["text"] == "こんにちは" and got["lang"] == "ja" and got["tier"] == "원격"
    assert calls[0]["base_url"] == "http://h:1" and calls[0]["lang"] is None
    assert calls[0]["bytes"] > 32000                 # 1 s of int16 wav
    # With the language pinned, that is what gets sent and what comes back.
    b = asr.OpenAIStreamASR(SPEC, "ko")
    b.transcribe(np.zeros(1600, dtype=np.float32), 16000)
    assert calls[1]["lang"] == "ko"


def test_remote_live_asr_joins_segments_and_needs_config():
    import pytest
    with pytest.raises(ValueError):
        asr.OpenAIStreamASR({"backend": "openai"}, None)
    assert asr.lang_code("Japanese") == "ja" and asr.lang_code("ko") == "ko"
    assert asr.lang_code("somethinglong") == "" and asr.lang_code(None) == ""


def test_live_asr_swaps_between_engines(monkeypatch):
    """What the session holds is one LiveASR, and only its insides change -- that is how
    run_stream and the Refiner keep looking at the same object and still use the new engine."""
    calls = []
    monkeypatch.setattr(asr, "post_transcription",
                        _fake_post(calls, {"text": "x", "language": "japanese"}))
    a = tcpp_asr.build_live_asr(SPEC, "ja")
    assert isinstance(a, tcpp_asr.LiveASR) and a.label == "원격" and a.forced_lang == "ja"
    a.transcribe(np.zeros(1600, dtype=np.float32), 16000)
    assert len(calls) == 1
    info = a.swap({**SPEC, "label": "원격2", "model": "large"})
    assert info["label"] == "원격2" and a.label == "원격2"
    a.transcribe(np.zeros(1600, dtype=np.float32), 16000)
    assert calls[1]["model"] == "large"
    # Switching to a local GGUF whose file is missing fails, and the one in use stays.
    import pytest
    with pytest.raises(FileNotFoundError):
        a.swap({"backend": "tcpp", "model": "no-such-model.gguf"})
    assert a.label == "원격2"


def test_idle_reap_drops_only_unused(monkeypatch):
    monkeypatch.setattr(models, "IDLE_S", 100.0)
    now = 1000.0
    monkeypatch.setattr(models.time, "time", lambda: now)
    a = models.shared(("a",), object)
    models.shared(("b",), object)
    now = 1090.0
    models.touch(("a",))                             # a is in use
    now = 1150.0
    gone = models.reap()
    assert gone == [("b",)]
    assert models.shared(("a",), object) is a        # a is unchanged
    assert models.shared(("b",), object) is not None  # b is built again


def test_idle_reap_is_off_by_default(monkeypatch):
    monkeypatch.setattr(models, "IDLE_S", 0.0)
    models.shared(("z",), object)
    assert models.reap(now=1e12) == []
