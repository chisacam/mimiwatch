"""Shared test setup.

No model is ever loaded. The store (`data/`) and the config (`backends.json`)
are redirected to a temporary directory for each test so that the real ones are
never touched -- test sessions used to survive in the real DB and show up in
the "Past streams" list on screen.

On a machine without the transcription runtimes (transcribe_cpp, sherpa_onnx),
such as CI, stub modules are installed so that an import still works. A test
that actually calls into them fails on the spot -- that means the test tried to
load a model, and the test is what needs fixing.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# The names that got a stub. Do not look them up again later with
# `importlib.util.find_spec` -- what goes into sys.modules below is a bare
# module with no __spec__, so that call raises ValueError right there. The side
# that installed the stub writing it down is the only accurate record.
_STUBBED: set[str] = set()


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
                raise RuntimeError("tests do not load transcription models")

        err.OutputTruncated, err.UnsupportedRequest = OutputTruncated, UnsupportedRequest
        tc.errors, tc.Model = err, Model
        tc.backend_available = lambda name: False
        sys.modules["transcribe_cpp"] = tc
        sys.modules["transcribe_cpp.errors"] = err
        _STUBBED.add("transcribe_cpp")
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        sys.modules["sherpa_onnx"] = types.ModuleType("sherpa_onnx")
        _STUBBED.add("sherpa_onnx")


_stub_native()


# The stubs above land in **this process's** sys.modules only. A test that runs
# the command line in a child process (test_cli_help) does not inherit them, so
# on a machine without the runtimes it died at `import sherpa_onnx` -- which
# reads as "the command line is broken" even though the parser is fine. For that
# side the same content is written out **as files** and handed over on
# PYTHONPATH. Only the missing ones are written, so on a machine where the
# runtimes are installed the real ones are used as before.
_SHERPA_STUB = '"""A fake for tests. Only the name has to exist -- every use site is inside a function."""\n'

_TCPP_STUB = '''"""A fake transcription runtime for tests. It imports, and dies if anything actually calls it."""
from .errors import OutputTruncated, UnsupportedRequest   # noqa: F401


class Model:
    def __init__(self, *a, **k):
        raise RuntimeError("tests do not load transcription models")


def backend_available(name):
    return False
'''

_TCPP_ERRORS_STUB = '''class OutputTruncated(Exception):
    pass


class UnsupportedRequest(Exception):
    pass
'''


def _write_native_stubs(d):
    """Write out only the runtimes that got a stub and return that path. None if there were none."""
    if "sherpa_onnx" in _STUBBED:
        (d / "sherpa_onnx.py").write_text(_SHERPA_STUB, encoding="utf-8")
    if "transcribe_cpp" in _STUBBED:
        pkg = d / "transcribe_cpp"
        pkg.mkdir(exist_ok=True)
        (pkg / "__init__.py").write_text(_TCPP_STUB, encoding="utf-8")
        (pkg / "errors.py").write_text(_TCPP_ERRORS_STUB, encoding="utf-8")
    return d if _STUBBED else None

import pytest  # noqa: E402

import config  # noqa: E402
import jobs  # noqa: E402
import live  # noqa: E402
import models  # noqa: E402
import store  # noqa: E402
import translate  # noqa: E402


@pytest.fixture(scope="session")
def native_stub_path(tmp_path_factory):
    """Path of the stub runtimes to hand to a child process. None if this machine has them all."""
    return _write_native_stubs(tmp_path_factory.mktemp("native_stubs"))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Redirect the store and the config to a temporary directory."""
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
    """Prefixes `T:` instead of translating. Records what was asked with which context."""

    name = "fake"

    def __init__(self, fn=None):
        self.calls: list[tuple[str, list[str]]] = []
        self.fn = fn or (lambda text, src, tgt, ctx: "T:" + text)

    def translate(self, text, src, tgt, context=None):
        self.calls.append((text, list(context or [])))
        return self.fn(text, src, tgt, context)


@pytest.fixture
def fake_translate(monkeypatch):
    """Fake out `translate.build`. Returns the translators it made."""
    made: list[FakeTranslator] = []

    def build(spec, genre=None, glossary=None):
        t = FakeTranslator()
        t.spec, t.genre = spec, genre
        made.append(t)
        return t

    monkeypatch.setattr(translate, "build", build)
    return made


def wait_job(job_id: str, timeout: float = 10.0) -> dict:
    """Wait until the job is done. It runs on a background thread, so poll."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = jobs.job_status(job_id)
        if st and st["state"] not in ("running",):
            return st
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s: {jobs.job_status(job_id)}")


@pytest.fixture
def session():
    """A live session that exercises the publish path alone, with no model. Events pile up in `emitted`."""
    s = live.LiveSession("https://example.invalid/live", "ja", "ko", "local-m2m100")
    s.emitted = []
    real_emit = s.emit

    def emit(e):
        s.emitted.append(e)
        real_emit(e)

    s.emit = emit
    # Keep the translation thread from starting. With a None translator `_translate` turns back at once.
    s._tr = None
    return s


@pytest.fixture
def fake_ffmpeg():
    """Stand-in for the ffmpeg process. n_chunks * 0.1 s of silence on stdout."""
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
