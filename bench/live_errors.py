"""Check what is left behind when a live session fails.

In the second report on issue #1, what showed up on screen and in the log was
not the real cause but `UnboundLocalError: cannot access local variable 'asr'`.
The `finally` in `_run` does `del asr, vad, history, refiner`, and if the try
fails before those names are created, that `del` itself blows up. Then

  - the real exception is buried under that error and there is no telling what
    went wrong, and
  - the `_release()` and `_retire()` that follow never run, so a 3GB model
    stays loaded.

Failures are common -- the URL is not live, yt-dlp is stale, the format cannot
be resolved. Every time the cause is hidden, a report leaves nowhere to look.

    .venv/bin/python bench/live_errors.py
"""
from __future__ import annotations

import os, sys, types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live                                                  # noqa: E402

FAIL = []


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def make_session():
    """The smallest session that can run _run without loading a model.

    **It uses the real constructor.** It used to build an empty object with
    `__new__` and list the attribute names by hand, but once multiview added
    `group`, `site` and `channel` to `__init__` that list went stale and
    `status()` died with an AttributeError -- the live code was fine and only
    the check was broken, and from here that reads as "engine swapping is
    broken". `__init__` is nothing but attribute assignment (it starts no
    thread, loads no model, writes to no store), so calling it outright is safe,
    and this check follows along as attributes are added.

    Only the two paths that reach outside are blocked: persisting state (the
    store) and emitting events (SSE).
    """
    s = live.LiveSession(
        url="https://example.invalid/x", lang="ja", viewer_lang="ko",
        backend_id="local-gemma", asr_backend_id="tcpp-best",
        profile="broadcast", genre="general", refine=True, source="hls")
    s.id = "t"                  # Pinned so the check output does not change from run to run
    s._persist = lambda: None
    s.emit = lambda e: None
    return s


def run_failing(monkey):
    """Make _run fail, and return both the exception that escaped and whether cleanup ran."""
    released, retired = [], []
    real_retire, real_sub = live._retire, live.subprocess
    # _retire now takes the session object (so it does not evict a resumed session of the same id).
    live._retire = lambda s: retired.append(getattr(s, "id", s))
    live.subprocess = monkey
    s = make_session()
    s._release = lambda: released.append(True)
    try:
        s._run()
        escaped = None
    except BaseException as e:                 # noqa: BLE001
        escaped = f"{type(e).__name__}: {e}"
    finally:
        live._retire, live.subprocess = real_retire, real_sub
    return s, escaped, released, retired


def main():
    print("[1] yt-dlp cannot be found (failing before asr is created)")
    boom = types.SimpleNamespace(
        run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("yt-dlp 없음")),
        Popen=None)
    s, escaped, released, retired = run_failing(boom)
    check(escaped is None, f"no exception escapes ({escaped})")
    check(s.state == "error", f"the state is error ({s.state})")
    check("yt-dlp" in (s.error or ""), f"the real cause survives ({s.error})")
    check(bool(released), "_release() is called (the model is let go)")
    check(retired == ["t"], f"_retire() is called ({retired})")

    print("\n[2] a URL that is not live (returning from inside the try)")
    class Done:
        returncode = 0
        stdout = '{"title": "x", "id": "y", "is_live": false}'
        stderr = ""
    vod = types.SimpleNamespace(run=lambda *a, **k: Done(), Popen=None)
    s, escaped, released, retired = run_failing(vod)
    check(escaped is None, f"no exception escapes ({escaped})")
    check(s.state == "error", f"the state is error ({s.state})")
    check("not live" in (s.error or ""), f"the notice survives ({s.error})")
    check(bool(released) and retired == ["t"], "cleanup runs")

    print("\n[3] does resolve_audio carry out what yt-dlp said")
    class NoFmt:
        returncode = 1
        stdout = ""
        stderr = "ERROR: [youtube] requested format is not available"
    real = live.subprocess
    live.subprocess = types.SimpleNamespace(run=lambda *a, **k: NoFmt(), Popen=None)
    try:
        live.resolve_audio("https://example.invalid/x")
        msg = ""
    except RuntimeError as e:
        msg = str(e)
    finally:
        live.subprocess = real
    check("requested format is not available" in msg,
          "what yt-dlp said is carried through verbatim")
    check("234" in msg, "which format was tried survives too")

    print("\n[4] does swapping the transcription engine keep the session and its cues")
    # This is the point. Changing the engine used to mean restarting the
    # session, and since cues are stored by session id the transcript up to
    # that moment disappeared.
    swapped = {}

    class FakeAsr:
        label = "old-model"
        def swap(self, spec):
            swapped["spec"] = spec
            self.label = "new-model"
            return {"label": self.label, "device": "cpu", "threads": 8}

    s = make_session()
    s._asr = FakeAsr()
    s.asr_label = "old-model"
    s._recent = [{"t": 1.0, "text": "이미 받아 적은 줄"}]
    before_id = s.id
    live._sessions[s.id] = s
    # config.py answers for the settings. live.py used to open backends.json
    # itself, so builtins.open had to be swapped out; now config is the only
    # place that reads that file, so swapping its function is enough.
    lite = {"id": "tcpp-lite", "backend": "tcpp", "model": "SenseVoiceSmall-Q8_0.gguf"}
    real_find_asr = live.config.find_asr
    live.config.find_asr = lambda bid: lite if bid == "tcpp-lite" else None
    try:
        res = live.set_asr(s.id, "tcpp-lite")
    finally:
        live.config.find_asr = real_find_asr
        live._sessions.pop(s.id, None)

    check(not res.get("error"), f"the swap went through ({res})")
    check(s.id == before_id, f"the session id is unchanged ({s.id})")
    check(s._recent == [{"t": 1.0, "text": "이미 받아 적은 줄"}],
          "the lines already transcribed are still there")
    check(s.asr_backend_id == "tcpp-lite" and s.asr_label == "new-model",
          f"the engine name changed ({s.asr_label})")
    check(swapped.get("spec", {}).get("model") == "SenseVoiceSmall-Q8_0.gguf",
          "the spec was passed through unchanged")

    print("\n[5] does a failed swap leave the engine in use as it was")
    class Refuses(FakeAsr):
        def swap(self, spec):
            raise RuntimeError("이 모델은 'ja' 언어를 지원하지 않습니다")
    s2 = make_session()
    s2._asr = Refuses()
    s2.asr_backend_id = "tcpp-best"
    s2.asr_label = "old-model"
    live._sessions[s2.id] = s2
    live.config.find_asr = lambda bid: lite if bid == "tcpp-lite" else None
    try:
        res2 = live.set_asr(s2.id, "tcpp-lite")
    finally:
        live.config.find_asr = real_find_asr
        live._sessions.pop(s2.id, None)
    check("error" in res2, f"the failure is reported ({res2.get('error', '')[:40]})")
    check(s2.asr_backend_id == "tcpp-best" and s2.asr_label == "old-model",
          "the engine in use is unchanged")

    print("\n[6] does an interrupted session resume as the same session")
    started = {}
    real_store_session, real_store_cues = live.store.session, live.store.cues
    # **The write path is blocked too.** With only the reads faked, the
    # _persist() inside resume() left a test session in the real DB, and it
    # showed up in the user's video list as an old broadcast. A check must not
    # touch the store.
    real_save_session, real_save_job = live.store.save_session, live.store.save_job
    live.store.save_session = lambda *a, **k: None
    live.store.save_job = lambda *a, **k: None
    live.store.session = lambda sid: {
        "id": sid, "state": "interrupted", "url": "https://example.invalid/live",
        "source_lang": "ja", "viewer_lang": "ko", "backend": "local-gemma",
        "asr_backend": "tcpp-best", "profile": "collab", "refine": True,
        "genre": "gaming", "title": "옛 방송", "video_id": "vid1",
        "media_base": 400.0, "audio_s": 1100.0,
    }
    live.store.cues = lambda sid: [{"id": 1}, {"id": 2}, {"id": 7}]
    real_start = live.LiveSession.start
    live.LiveSession.start = lambda self: started.setdefault("self", self)
    try:
        res = live.resume("sess-1")
        s = started.get("self")
    finally:
        live.LiveSession.start = real_start
        live.store.session, live.store.cues = real_store_session, real_store_cues
        live.store.save_session, live.store.save_job = real_save_session, real_save_job
        live._sessions.pop("sess-1", None)

    check(not res.get("error"), f"the resume was accepted ({res})")
    check(s is not None and s.id == "sess-1", "it keeps the session id")
    check(s and s._seq == 7, f"the numbering carries on (_seq={s and s._seq})")
    check(s and s.lines == 3, f"the line count is inherited ({s and s.lines})")
    check(s and abs(s.resume_from - 1500.0) < 0.01,
          f"the interrupted media position is worked out ({s and s.resume_from})")
    check(s and s.profile == "collab" and s.genre == "gaming" and s.refine,
          "the settings are inherited unchanged")

    print("\n[7] when resume is not possible")
    live.store.save_session = lambda *a, **k: None
    live.store.session = lambda sid: None
    check("error" in live.resume("no-such-session"), "a missing session is refused")
    live.store.session = lambda sid: {"id": sid, "state": "running", "url": "x"}
    check("error" in live.resume("sess-1"), "a session already running is refused")
    live.store.session = lambda sid: {"id": sid, "state": "interrupted", "url": ""}
    check("error" in live.resume("sess-1"), "a session with no URL is refused")
    live.store.session = real_store_session
    live.store.save_session = real_save_session

    # Confirm for ourselves that the check left nothing in the store.
    check(live.store.session("sess-1") is None,
          "no test session was left in the store")

    print("\n[8] does saving a cue with None values keep the session alive")
    # Leaving the source language to auto-detection lets lang flow through as
    # None. The cues column is NOT NULL, and `.get(k, "")` does **not** hand
    # back the default when the key is present and the value is None, so None
    # was bound straight through and the session ended on the first final line.
    # That loses not one subtitle line but the whole broadcast.
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    real_db, real_data, real_conn = live.store.DB, live.store.DATA, live.store._db
    live.store.DB = os.path.join(tmp, "t.db")
    live.store.DATA = tmp
    live.store._db = None
    try:
        live.store.init()
        live.store.save_cue("s", {"id": 1, "kind": "final", "t": 1.0,
                                  "text": "안녕", "lang": None, "speaker": None})
        row = live.store.cues("s")[0]
        check(row["lang"] == "" and row["speaker"] == "",
              f"None is stored as an empty string (lang={row['lang']!r})")
        live.store.save_cue("s", {"id": 2, "kind": None, "t": None,
                                  "text": None, "lang": None, "speaker": None})
        check(len(live.store.cues("s")) == 2, "it is stored even when everything is None")
    except Exception as exc:                                # noqa: BLE001
        check(False, f"saving raised an exception: {type(exc).__name__}: {exc}")
    finally:
        live.store.DB, live.store.DATA, live.store._db = real_db, real_data, real_conn
        shutil.rmtree(tmp, ignore_errors=True)

    # Even when left to auto-detection, the adapter must not leak a None.
    import tcpp_asr
    a = tcpp_asr.TranscribeCppASR.__new__(tcpp_asr.TranscribeCppASR)
    for lang in (None, "", "ja"):
        a.forced_lang = lang or ""
        check(a.forced_lang is not None and isinstance(a.forced_lang, str),
              f"forced_lang is a string (input {lang!r} -> {a.forced_lang!r})")

    print()
    if FAIL:
        print(f"{len(FAIL)} failed")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
