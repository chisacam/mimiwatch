"""라이브 인식기: 원격(OpenAI 호환) 어댑터와 갈아 끼우기, 유휴 언로드."""
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
    assert calls[0]["bytes"] > 32000                 # 1초 int16 wav
    # 언어를 고정했으면 그것을 보내고 그것을 돌려줍니다.
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
    """세션이 쥔 것은 LiveASR 하나이고, 속만 바뀝니다 -- run_stream과 Refiner가
    같은 객체를 계속 보면서도 새 엔진을 쓰게 되는 이유입니다."""
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
    # 로컬 GGUF 로 바꾸려는데 파일이 없으면 실패하고 쓰던 것이 남습니다.
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
    models.touch(("a",))                             # a 는 쓰는 중
    now = 1150.0
    gone = models.reap()
    assert gone == [("b",)]
    assert models.shared(("a",), object) is a        # a 는 그대로
    assert models.shared(("b",), object) is not None  # b 는 다시 만듭니다


def test_idle_reap_is_off_by_default(monkeypatch):
    monkeypatch.setattr(models, "IDLE_S", 0.0)
    models.shared(("z",), object)
    assert models.reap(now=1e12) == []
