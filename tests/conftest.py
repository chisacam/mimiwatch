"""시험 공통 준비.

모델은 올리지 않습니다. 저장소(`data/`)와 설정(`backends.json`)은 시험마다
임시 디렉터리로 돌려 진짜 것을 건드리지 않습니다 -- 예전에 시험용 세션이
진짜 DB에 남아 화면의 「지난 방송」 목록에 뜬 적이 있습니다.

전사 런타임(transcribe_cpp, sherpa_onnx)이 없는 기계(CI)에서는 가짜 모듈을
끼워 import만 되게 합니다. 시험이 그것들을 실제로 부르면 곧바로 실패합니다 --
그러면 그 시험이 모델을 올리려 한 것이니 고쳐야 합니다.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _stub_native():
    try:
        import transcribe_cpp  # noqa: F401
    except ImportError:
        tc = types.ModuleType("transcribe_cpp")
        err = types.ModuleType("transcribe_cpp.errors")

        class OutputTruncated(Exception):
            pass

        class UnsupportedRequest(Exception):
            pass

        class Model:
            def __init__(self, *a, **k):
                raise RuntimeError("시험은 전사 모델을 올리지 않습니다")

        err.OutputTruncated, err.UnsupportedRequest = OutputTruncated, UnsupportedRequest
        tc.errors, tc.Model = err, Model
        tc.backend_available = lambda name: False
        sys.modules["transcribe_cpp"] = tc
        sys.modules["transcribe_cpp.errors"] = err
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        sys.modules["sherpa_onnx"] = types.ModuleType("sherpa_onnx")


_stub_native()

import pytest  # noqa: E402

import config  # noqa: E402
import jobs  # noqa: E402
import live  # noqa: E402
import models  # noqa: E402
import store  # noqa: E402
import translate  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """저장소와 설정을 임시 디렉터리로."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(store, "DATA", str(data))
    monkeypatch.setattr(store, "DB", str(data / "mimiwatch.db"))
    monkeypatch.setattr(store, "_db", None)
    monkeypatch.setattr(jobs, "DATA", str(data))
    monkeypatch.setattr(config, "CONFIG", str(tmp_path / "backends.json"))
    store.init()
    models.clear()
    live._sessions.clear()
    yield tmp_path
    if store._db is not None:
        store._db.close()
        store._db = None
    live._sessions.clear()


class FakeTranslator(translate.Translator):
    """번역 대신 `T:` 를 앞에 붙입니다. 무엇을 어떤 문맥으로 물었는지 남깁니다."""

    name = "fake"

    def __init__(self, fn=None):
        self.calls: list[tuple[str, list[str]]] = []
        self.fn = fn or (lambda text, src, tgt, ctx: "T:" + text)

    def translate(self, text, src, tgt, context=None):
        self.calls.append((text, list(context or [])))
        return self.fn(text, src, tgt, context)


@pytest.fixture
def fake_translate(monkeypatch):
    """`translate.build`를 가짜로. 만들어진 번역기들을 돌려줍니다."""
    made: list[FakeTranslator] = []

    def build(spec, genre=None):
        t = FakeTranslator()
        t.spec, t.genre = spec, genre
        made.append(t)
        return t

    monkeypatch.setattr(translate, "build", build)
    return made


def wait_job(job_id: str, timeout: float = 10.0) -> dict:
    """작업이 끝날 때까지 기다립니다. 배경 스레드라 폴링합니다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jobs.job_status(job_id)
        if st and st["state"] not in ("running",):
            return st
        time.sleep(0.02)
    raise AssertionError(f"작업 {job_id}가 {timeout}초 안에 끝나지 않았습니다: {jobs.job_status(job_id)}")


@pytest.fixture
def session():
    """모델 없이 발행 경로만 시험할 수 있는 라이브 세션. 이벤트는 `emitted`에 쌓입니다."""
    s = live.LiveSession("https://example.invalid/live", "ja", "ko", "local-m2m100")
    s.emitted = []
    real_emit = s.emit

    def emit(e):
        s.emitted.append(e)
        real_emit(e)

    s.emit = emit
    # 번역 스레드가 뜨지 않게. 번역기가 None이면 `_translate`가 곧 돌아섭니다.
    s._tr = None
    return s


@pytest.fixture
def fake_ffmpeg():
    """ffmpeg 프로세스 흉내. stdout에 n_chunks * 0.1초의 무음."""
    import io

    class FakeFF:
        def __init__(self, n_chunks):
            self.stdout = io.BytesIO(b"\x00" * (live.CHUNK * 2 * n_chunks))

        def terminate(self):
            pass

        def kill(self):
            pass

    return FakeFF


__all__ = ["FakeTranslator", "wait_job", "threading"]
