"""The place that keeps exactly one copy of a heavy model resident in the process.

Sessions and jobs each used to load their own model. Every call to
`translate.build()` made a new Gemma (4.9GB) and a new fallback M2M-100
(473MB), and every live session loaded a new Whisper (845MB). One live session
overlapping one retranslation job meant two copies of Gemma, and because they
were let go when the session ended, the first subtitle of the next session was
late by the few seconds it took to load the model again.

Here a (kind, path, device, …) key builds it once and hands it round. Things
that are not the model itself, like the prompt or the thread count, do not go
into the key -- two sessions with different genres should share one Gemma.

**Resident is the default.** The job of this tool is exactly to run these
models, so resident is right, and letting go at the end of every session brings
the delay above back.

**Give `MIMIWATCH_MODEL_IDLE_S` and a model idle for that long is let go.** It
is an option for someone who does other work on this machine too -- there is no
reason for Gemma to be holding 5GB two hours after the stream was watched to the
end. "Idle" means neither `shared()` nor `touch()` was called even once in that
time. The users (transcription, translation) call `touch()` on every call, so
nothing is let go while a two-hour stream is running. Letting go means taking it
out of this table; the memory itself comes back when the last session or job
holding it lets go of its hand. The next request loads it again.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Hashable, TypeVar

T = TypeVar("T")

# 0 means never let go (the default). In seconds.
IDLE_S = float(os.environ.get("MIMIWATCH_MODEL_IDLE_S") or 0)

_lock = threading.Lock()
_cache: dict[Hashable, object] = {}
_last: dict[Hashable, float] = {}
# One per key. When two threads ask for the same model for the first time at
# once, only one of them builds it and the other waits -- holding the global
# lock while loading 4.9GB also stalls whoever asked for a different model
# in the meantime.
_building: dict[Hashable, threading.Lock] = {}
_reaper: threading.Thread | None = None


def shared(key: Hashable, factory: Callable[[], T]) -> T:
    """Returns the model for `key`. If there is none, `factory()` builds it once."""
    with _lock:
        got = _cache.get(key)
        if got is not None:
            _last[key] = time.time()
            return got
        gate = _building.setdefault(key, threading.Lock())
    with gate:
        with _lock:
            got = _cache.get(key)
            if got is not None:
                _last[key] = time.time()
                return got
        obj = factory()
        with _lock:
            _cache[key] = obj
            _last[key] = time.time()
            _building.pop(key, None)
            _ensure_reaper()
        return obj


def touch(key: Hashable):
    """Notes that `key` is being used right now. Winds back the idle-unload clock."""
    if IDLE_S <= 0:
        return
    with _lock:
        if key in _cache:
            _last[key] = time.time()


def reap(now: float | None = None, idle_s: float | None = None) -> list[Hashable]:
    """Takes models idle longer than `idle_s` out of the table. Returns the keys removed."""
    idle = IDLE_S if idle_s is None else idle_s
    if idle <= 0:
        return []
    now = time.time() if now is None else now
    with _lock:
        gone = [k for k, t in _last.items() if k in _cache and now - t >= idle]
        for k in gone:
            _cache.pop(k, None)
            _last.pop(k, None)
    for k in gone:
        print(f"[models] {idle:.0f}초 놀려서 놓아줍니다: {' · '.join(str(p) for p in k)}",
              file=sys.stderr, flush=True)
    return gone


def _ensure_reaper():
    """If idle unloading is on, start one watcher thread. Called inside `_lock`."""
    global _reaper
    if IDLE_S <= 0 or (_reaper is not None and _reaper.is_alive()):
        return

    def loop():
        period = max(5.0, min(60.0, IDLE_S / 2))
        while True:
            time.sleep(period)
            reap()

    _reaper = threading.Thread(target=loop, daemon=True, name="models-reaper")
    _reaper.start()


def resident() -> list[str]:
    """The keys of the models loaded right now. bench/doctor.py prints them."""
    with _lock:
        return [" · ".join(str(k) for k in key) for key in _cache]


def clear():
    """Lets go of everything. For keeping tests from affecting one another."""
    with _lock:
        _cache.clear()
        _last.clear()
        _building.clear()
