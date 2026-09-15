"""The live session's publish path and its reconnects. No model is loaded."""
import json
import numpy as np
import os
import pytest
import subprocess
import threading
import time
import wave

import live
import store


def test_refine_absorbs_the_finals_it_covers(session):
    s = session
    s.publish_line("final", "こんにちは", "ja", "")
    s.audio_s = 2.0
    s.publish_line("final", "元気ですか", "ja", "")
    s.audio_s = 4.0
    s.publish_line("refine", "こんにちは 元気ですか", "ja", "")
    rows = store.cues(s.id)
    assert [(c["id"], c["kind"], c["text"]) for c in rows] == [(1, "refine", "こんにちは 元気ですか")]
    ref = [e for e in s.emitted if e.get("kind") == "refine"][0]
    assert ref["replaces"] == [1, 2] and ref["id"] == 1 and ref["t"] == 0.0


def test_refine_without_a_match_becomes_a_new_line(session):
    s = session
    s.publish_line("final", "全然違う文章です", "ja", "")
    s.audio_s = 3.0
    s.publish_line("refine", "まったく別の内容", "ja", "")
    rows = store.cues(s.id)
    # It used to reuse the most recent line's number (1) and overwrite that line.
    assert [(c["id"], c["text"]) for c in rows] == [(1, "全然違う文章です"), (2, "まったく別の内容")]
    assert s.lines == 2


def test_short_final_needs_exact_containment():
    assert live._covers("はい", "はい、そうです") is True
    assert live._covers("はい", "いいえ") is False
    assert live._covers("무기도 풀제열이야", "무기도 풀제일이야?") is True   # A few letters apart still counts as covered


def test_notes_are_not_translation_context(session):
    s = session
    s.publish_line("note", "⋯ about 10 s went unreceived while the server was down ⋯",
                   "ja", "")
    s.audio_s = 1.0
    s.publish_line("final", "a", "ja", "")
    assert s._context_for({"t": 5.0}) == ["a"]


def test_event_log_replay(session):
    s = session
    for n in range(3):
        s.emit({"type": "x", "n": n})
    assert [seq for seq, _ in s.replay_since("1")] == [2, 3]
    assert [seq for seq, _ in s.replay_since("0")] == [1, 2, 3]
    assert s.replay_since("3") == []
    assert s.replay_since("99") is None          # Outside the log: the whole backlog
    assert s.replay_since("abc") is None


def test_stop_marks_user_and_status_carries_it(session):
    s = session
    s.stop()
    assert s.stopped_by == "user" and s.status()["stopped_by"] == "user"


def _scenario(session, fake_ffmpeg, outcomes, tries=3):
    """Run the reader thread (`_read_loop`) to the end right here, then watch what the
    transcribing side (`_consume`) takes out of the ring. In reality the two are
    separate threads, but the ring keeps the order, so running them one after the
    other gives the same result."""
    s = session
    live.HLS_RECONNECT_TRIES = tries
    s._ff = fake_ffmpeg(3)
    s._recv_base = s.media_base = 100.0
    resolves = []

    def fake_resolve(reconnect=False):
        assert reconnect
        resolves.append(round(s.resume_from, 3))
        o = outcomes.pop(0)
        if o == "ended":
            return None, None
        if o == "fail":
            raise RuntimeError("network")
        s.gap_s = 7.0
        s._recv_base = 200.0
        return "src", -2
    s._resolve_hls = fake_resolve
    s._spawn_ffmpeg = lambda src, idx: setattr(s, "_ff", fake_ffmpeg(2))
    s._stop.wait = lambda t: False
    s._read_loop()
    assert s._ended
    seq = "".join("S" if isinstance(c, np.ndarray) else "N" for c in s._consume())
    return seq, resolves


def test_hls_reconnects_inside_the_session(session, fake_ffmpeg):
    seq, resolves = _scenario(session, fake_ffmpeg, ["ok", "ended"])
    assert seq == "SSSNSSN"                      # 3 frames, flush, 2 more frames, flush
    assert session.stopped_by == "ended" and session.state == "stopped"
    assert resolves[0] == 100.3                  # Where it broke = base + the 0.3 s received
    assert abs(resolves[1] - 200.2) < 1e-6       # It ended 0.2 s after the new base
    notes = [c for c in store.cues(session.id) if c["kind"] == "note"]
    assert len(notes) == 1 and "7 s" in notes[0]["text"]
    # The note is published **after** the new time base has travelled through the
    # ring and taken effect. Slipped in among the old frames with a timestamp in
    # the 200 s range it would sit out of order in the transcript.
    assert notes[0]["t"] == 200.0
    # The transcribing clock has also reached the last frame on the new base.
    assert session.media_base == 200.0 and abs(session.audio_s - 0.2) < 1e-6


def test_hls_gives_up_after_retries(session, fake_ffmpeg):
    seq, _ = _scenario(session, fake_ffmpeg, ["fail", "fail", "fail"], tries=3)
    assert seq == "SSSN"
    assert session.stopped_by == "stream" and session.state == "error"
    assert "Resume" in session.error


def test_user_stop_does_not_reconnect(session, fake_ffmpeg):
    s = session
    s._ff = fake_ffmpeg(2)
    read = s._ff.stdout.read

    def read_then_stop(n):          # The user hits "stop" right after the first frame is read
        raw = read(n)
        s.stop()
        return raw
    s._ff.stdout.read = read_then_stop
    s._resolve_hls = lambda reconnect=False: pytest.fail("tried to reconnect after the stop")
    s._read_loop()
    # The transcribing side sees the stop flag and does not touch the remaining frames.
    assert list(s._consume()) == []
    assert s.stopped_by == "user" and s.state == "stopped"


def test_ring_drops_oldest_audio_but_keeps_markers():
    r = live.Ring(1.0)                           # 10 frames
    frame = np.zeros(live.CHUNK, dtype=np.float32)
    for i in range(1, 8):
        r.push(("audio", i * 0.1, frame))
    r.push(("flush",))
    for i in range(8, 16):
        r.push(("audio", i * 0.1, frame))
    assert abs(r.seconds() - 1.0) < 1e-9 and abs(r.dropped_s - 0.5) < 1e-9
    items = []
    while True:
        it = r.pop(timeout=0)
        if it is live.Ring.TIMEOUT:
            break
        items.append(it)
    kinds = [it[0] for it in items]
    assert kinds.count("audio") == 10 and kinds.index("flush") == 2   # The marker sits after 0.6 and 0.7
    assert abs(items[0][1] - 0.6) < 1e-9         # The oldest went first


def test_consumer_clock_follows_the_reader_after_a_gap(session):
    """Even after the ring overflows and drops frames, a cue's time is where that frame sat in the stream."""
    s = session
    s._ring = live.Ring(1.0)
    s.media_base = 100.0
    frame = np.zeros(live.CHUNK, dtype=np.float32)
    for i in range(1, 31):                       # 3 s came in but only 1 s survives
        s._recv_s = i * 0.1
        s._ring.push(("audio", s._recv_s, frame))
    s._ended = True
    s._ring.push(("end",))
    got = [c for c in s._consume() if isinstance(c, np.ndarray)]
    assert len(got) == 10
    assert abs(s.audio_s - 3.0) < 1e-9           # The reader's clock, not the sum of what was kept (1.0)
    s.publish_line("final", "こんにちは", "ja", "")
    assert store.cues(s.id)[-1]["t"] == 103.0


def test_delete_refuses_running_session(session):
    live._sessions[session.id] = session
    assert "error" in live.delete(session.id)
    live._sessions.clear()
    store.save_session(session.status())
    assert live.delete(session.id) == {"deleted": session.id}
    assert live.delete(session.id) == {"error": "no such session"}


# ---- Translation order and lines a refined line covers ---------------------------

class _SlowTranslator:
    """A translator that records the call order and leaves room for a refined line to arrive mid-way."""

    def __init__(self, delay=0.0, hook=None):
        self.calls = []
        self.delay = delay
        self.hook = hook

    def should_translate(self, text, src, tgt):
        return True

    def translate(self, text, src, tgt, context=None):
        self.calls.append(text)
        if self.hook:
            self.hook(text)
        import time
        time.sleep(self.delay)
        return "T:" + text


def _translations(s):
    return [(e["id"], e["text"]) for e in s.emitted if e.get("type") == "translation"]


def test_translations_arrive_in_publish_order(session):
    s = session
    s._tr = _SlowTranslator()
    for n in range(6):
        s.audio_s = n * 1.0
        s.publish_line("final", f"line {n}", "ja", "")
    s._close_translator()
    assert [t for _, t in _translations(s)] == [f"T:line {n}" for n in range(6)]
    assert s.translated == 6


def test_superseded_final_is_not_translated_after_refine(session):
    """The race where a late translation of a final line absorbed by a refined line overwrote the refined line's translation.

    While the translator is working on the first line, a refined line absorbs it.
    That stale translation used to be published and stored under the same id (the
    one the refined line inherited), so a partial translation was left under the
    refined line both on screen and in the DB.
    """
    s = session
    fired = []

    def during_first(text):
        if text == "こんにちは" and not fired:
            fired.append(1)
            s.audio_s = 3.0
            s.publish_line("refine", "こんにちは 元気ですか", "ja", "")

    s._tr = _SlowTranslator(hook=during_first)
    s.publish_line("final", "こんにちは", "ja", "")
    s.audio_s = 1.5
    s.publish_line("final", "元気ですか", "ja", "")
    # The refined line queues up while the first line is being translated, so wait
    # for it to be handled before putting in the end marker (when a session ends the
    # refiner closes first, so in practice this order holds by itself).
    import time
    deadline = time.time() + 5
    while time.time() < deadline and len(s._tr.calls) < 2:
        time.sleep(0.01)
    s._close_translator()
    got = _translations(s)
    # Both finals' translations are dropped; only the refined line's (id 1) goes out.
    assert got == [(1, "T:こんにちは 元気ですか")]
    rows = store.cues(s.id)
    assert [(c["id"], c["translations"]) for c in rows] == \
        [(1, {"local-m2m100": "T:こんにちは 元気ですか"})]
    # The absorbed second line never even called the translator -- that saves Gemma time.
    assert s._tr.calls == ["こんにちは", "こんにちは 元気ですか"]


def test_close_translator_drains_pending_lines(session):
    s = session
    s._tr = _SlowTranslator(delay=0.01)
    for n in range(5):
        s.audio_s = n * 1.0
        s.publish_line("final", f"tail {n}", "ja", "")
    s._release()                                  # What gets called when a session ends
    assert len(_translations(s)) == 5
    assert s._tr is None


def test_resume_uses_the_engines_the_caller_picked(monkeypatch):
    """Resume with whatever engine was picked in "Manage" while it was stopped. An unknown id falls back to the stored one."""
    st = {"id": "resume-1", "state": "stopped", "stopped_by": "user", "url": "https://x/live",
          "source": "hls", "source_lang": "ja", "viewer_lang": "ko", "backend": "local-m2m100",
          "asr_backend": "tcpp-lite", "media_base": 10.0, "audio_s": 5.0, "lines": 0}
    store.save_session(st, "")
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)   # Nothing is actually received
    got = live.resume("resume-1", asr_backend_id="tcpp-best", backend_id="local-gemma")
    assert got["resumed"]
    s = live.get("resume-1")
    assert s.asr_backend_id == "tcpp-best" and s.backend_id == "local-gemma"
    live._sessions.clear()
    store.save_session(st, "")                 # _run is a fake, so the state was left at starting
    got = live.resume("resume-1", asr_backend_id="no-such", backend_id="")
    s = live.get("resume-1")
    assert s.asr_backend_id == "tcpp-lite" and s.backend_id == "local-m2m100"
    live._sessions.clear()


def test_resume_can_switch_the_audio_source(monkeypatch):
    """Resume a URL session as tab audio and a tab-audio session as a URL. The subtitles stay in the same session."""
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)
    base = {"state": "stopped", "stopped_by": "user", "source_lang": "ja", "viewer_lang": "ko",
            "backend": "local-m2m100", "asr_backend": "tcpp-lite", "media_base": 0.0,
            "audio_s": 12.0, "lines": 0}
    store.save_session({**base, "id": "hls-1", "url": "https://x/live", "source": "hls"}, "")
    got = live.resume("hls-1", source="tab")
    s = live.get("hls-1")
    assert got["source"] == "tab" and s.source == "tab" and s.resume_from == 12.0
    live._sessions.clear()

    store.save_session({**base, "id": "tab-1", "url": "", "source": "tab"}, "")
    assert "error" in live.resume("tab-1", source="hls")            # There is no URL
    got = live.resume("tab-1", source="hls", url="https://y/live")
    s = live.get("tab-1")
    assert got["source"] == "hls" and s.url == "https://y/live"
    live._sessions.clear()


def test_tab_feed_slices_into_frames_and_idle_flushes_once():
    """For a tab session, feed() is the reader. A 2 s block becomes twenty 0.1 s frames in
    the ring; when the sound stops it finalises the pending utterance exactly once (None),
    and on "stop" it withdraws."""
    s = live.LiveSession("", None, "ko", "local-m2m100", source="tab", title="t")
    s._tr = None
    r = s.feed(b"\x00" * (live.CHUNK * 2 * 20))
    assert r["ok"] and r["queued_s"] == 2.0 and r["dropped_s"] == 0
    gen = s._consume()
    got = [next(gen) for _ in range(20)]
    assert all(isinstance(c, np.ndarray) and len(c) == live.CHUNK for c in got)
    assert abs(s.audio_s - 2.0) < 1e-9
    live.Ring.pop, real_pop = (lambda self, timeout: live.Ring.TIMEOUT), live.Ring.pop
    try:
        assert next(gen) is None                 # 2 s of silence (0.5 s x 4): flush once
    finally:
        live.Ring.pop = real_pop
    s.stop()
    assert list(gen) == []
    assert s.state == "stopped" and s.feed(b"\x00" * 3200)["error"]


def test_tab_ring_drops_oldest_when_the_browser_outruns_asr():
    s = live.LiveSession("", None, "ko", "local-m2m100", source="tab", title="t")
    s._ring.set_max(1.0)                         # Just 1 s for the test
    r = s.feed(b"\x00" * (live.CHUNK * 2 * 15))
    assert r["queued_s"] == 1.0 and abs(r["dropped_s"] - 0.5) < 1e-9


# ---- Focus: only one session transcribes at a time --------------------------------

def test_unfocus_flushes_and_ends_the_generator(session):
    s = session
    frame = np.zeros(live.CHUNK, dtype=np.float32)
    for i in range(1, 4):
        s._ring.push(("audio", i * 0.1, frame))
    gen = s._consume()
    assert isinstance(next(gen), np.ndarray)
    s.set_focus(False)
    rest = list(gen)
    assert rest[-1] is None                      # Finalise the pending utterance, then withdraw
    assert s.state == "starting" and not s._ended and s._ring.seconds() > 0   # The session itself is untouched
    assert s.status()["focused"] is False
    assert s.emitted[-1]["type"] == "status" and s.emitted[-1]["focused"] is False


def _fake_engines(monkeypatch, log):
    class FakeRefiner:
        def __init__(self, asr, history, sink):
            self.sid = sink.s.id
        def close(self, timeout=10.0):
            log.append((self.sid, "refiner.close"))
    def fake_run_stream(chunks, vad, asr, sink, history, refiner=None):
        sid = sink.s.id
        log.append((sid, "start"))
        n = sum(1 for c in chunks if isinstance(c, np.ndarray))
        log.append((sid, "end", n))
    monkeypatch.setattr(live, "build_vad", lambda **kw: object())
    monkeypatch.setattr(live, "Refiner", FakeRefiner)
    monkeypatch.setattr(live, "run_stream", fake_run_stream)
    monkeypatch.setattr(live.LiveSession, "_ensure_engines", lambda self: setattr(self, "_asr", object()))
    monkeypatch.setattr(live.LiveSession, "_start_reader", lambda self, src, idx: None)


def test_only_one_session_transcribes_and_the_old_one_stops_first(monkeypatch):
    log = []
    _fake_engines(monkeypatch, log)
    a = live.LiveSession("https://x/a", "ja", "ko", "local-m2m100"); a._tr = None
    b = live.LiveSession("https://x/b", "ja", "ko", "local-m2m100"); b._tr = None
    b.set_focus(False)                           # b is a waiting session that only receives sound
    frame = np.zeros(live.CHUNK, dtype=np.float32)
    for i in range(1, 21):
        b._ring.push(("audio", i * 0.1, frame))
    ta = threading.Thread(target=a._transcribe, args=("src", -2), daemon=True)
    tb = threading.Thread(target=b._transcribe, args=("src", -2), daemon=True)
    ta.start(); tb.start()
    deadline = time.time() + 5
    while (a.id, "start") not in log and time.time() < deadline:
        time.sleep(0.05)
    assert (a.id, "start") in log and not any(e[0] == b.id for e in log)   # b waits
    # Move the focus to b. The old one goes off first, then the new one on (multiview_focus's order).
    a.set_focus(False)
    b.set_focus(True)
    deadline = time.time() + 5
    while (b.id, "start") not in log and time.time() < deadline:
        time.sleep(0.05)
    kinds = [(e[0], e[1]) for e in log]
    assert kinds.index((a.id, "end")) < kinds.index((a.id, "refiner.close")) < kinds.index((b.id, "start"))
    a.stop(); b.stop()
    ta.join(5); tb.join(5)
    assert not ta.is_alive() and not tb.is_alive()
    ended_b = [e for e in log if e[0] == b.id and e[1] == "end"]
    assert ended_b and ended_b[0][2] == 20      # The twenty frames that pooled in the ring while waiting were transcribed first
    assert a.state == "stopped" and b.state == "stopped"
    assert not live._transcriber.locked()


def test_set_asr_on_a_warm_session_records_the_engine(session):
    s = session
    live._sessions[s.id] = s
    try:
        assert s._asr is None
        got = live.set_asr(s.id, "tcpp-lite")
        assert got["asr"] == "tcpp-lite" and s.asr_backend_id == "tcpp-lite"
        assert "error" in live.set_asr(s.id, "no-such")
    finally:
        live._sessions.clear()


def test_resume_prefers_the_received_position(monkeypatch):
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)
    base = {"state": "stopped", "stopped_by": "user", "url": "https://x/live", "source": "hls",
            "source_lang": "ja", "viewer_lang": "ko", "backend": "local-m2m100",
            "asr_backend": "tcpp-lite", "media_base": 10.0, "audio_s": 5.0, "lines": 0}
    store.save_session({**base, "id": "warm-1", "recv_t": 500.0}, "")
    live.resume("warm-1")
    assert live.get("warm-1").resume_from == 500.0
    live._sessions.clear()
    store.save_session({**base, "id": "old-1"}, "")   # An old record has no recv_t
    live.resume("old-1")
    assert live.get("old-1").resume_from == 15.0
    live._sessions.clear()


# ---- Multiview bundles ------------------------------------------------------------

def _drain(q):
    out = []
    while True:
        try:
            out.append(__import__("json").loads(q.get_nowait()))
        except Exception:
            return out


def test_multiview_groups_sessions_and_moves_focus(monkeypatch):
    import bus
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)   # Nothing is actually received
    q = bus.subscribe()
    try:
        mk = lambda u: live._new_session(u, "ja", "ko", "local-m2m100", "broadcast", "", True, None, "hls", "")
        outsider, a = mk("https://x/out"), mk("https://x/a")
        g = live.multiview_start([{"session": a.id}, {"url": "https://x/b"}],
                                 "ja", "ko", "local-m2m100", focus=a.id)
        gid = g["id"]
        assert g["focus"] == a.id and len(g["members"]) == 2
        b = live.get(g["members"][1]["id"])
        assert a.group == gid and b.group == gid and b.url == "https://x/b"
        assert a._focus.is_set() and not b._focus.is_set()      # The one already being watched takes the focus, the new one waits
        assert outsider._stop.is_set() and outsider.stopped_by == "user"   # Anything outside the bundle stops
        assert not a._stop.is_set() and not b._stop.is_set()
        assert any(e["type"] == "multiview" and e["focus"] == a.id for e in _drain(q))

        got = live.multiview_focus(gid, b.id)
        assert got["previous"] == a.id and got["focus"] == b.id
        assert b._focus.is_set() and not a._focus.is_set()
        assert live.multiview_focus(gid, "nope")["error"]
        assert live.multiview_focus("nope", b.id)["error"]

        c = live.get(live.multiview_add(gid, {"url": "https://x/c"}, "ja", "ko", "local-m2m100")["id"])
        assert c.group == gid and not c._focus.is_set() and len(live.multiview_status(gid)["members"]) == 3

        # Closing the focused tile stops that session and the focus moves to the first remaining member
        got = live.multiview_remove(gid, b.id)
        assert b._stop.is_set() and got["focus"] == a.id and a._focus.is_set() and b.group == ""

        # The bundle follows even when a session ends on its own (_retire). When the last member ends the bundle goes too.
        live._retire(a)
        assert live.multiview_status(gid)["focus"] == c.id and c._focus.is_set() and a.group == ""
        live._retire(c)
        assert live.multiview_status(gid) is None
        assert any(e["type"] == "multiview" and e.get("deleted") for e in _drain(q))

        # A bad bundle request leaves nothing behind
        assert live.multiview_start([], "ja", "ko", "local-m2m100")["error"]
        assert live.multiview_start([{"url": f"https://x/{i}"} for i in range(5)], "ja", "ko", "local-m2m100")["error"]
        before = set(live._sessions)
        assert live.multiview_start([{"url": "https://x/d"}, {"session": "nope"}], "ja", "ko", "local-m2m100")["error"]
        leftovers = [live.get(i) for i in set(live._sessions) - before]
        assert all(s._stop.is_set() and s.group == "" for s in leftovers)
        assert not live._groups

        # Receiving alone (start) still stops everything as before -- bundle members included
        g2 = live.multiview_start([{"url": "https://x/e"}, {"url": "https://x/f"}], "ja", "ko", "local-m2m100")
        e, f = (live.get(m["id"]) for m in g2["members"])
        assert e._focus.is_set() and not f._focus.is_set()        # Without a focus given, the first member
        live.start("https://x/solo", "ja", "ko", "local-m2m100")
        assert e._stop.is_set() and f._stop.is_set()
        live.multiview_stop(g2["id"])
        assert live.multiview_status(g2["id"]) is None
    finally:
        bus.unsubscribe(q)
        live._sessions.clear()
        live._groups.clear()


def test_site_of_tells_the_embed_apart():
    yt = {"extractor_key": "Youtube", "webpage_url_domain": "youtube.com", "id": "abc123XYZ_-",
          "channel_id": "UCx"}
    assert live.site_of(yt) == {"site": "youtube", "video_id": "abc123XYZ_-",
                                "channel": "UCx", "play_url": ""}
    tw = {"extractor_key": "TwitchStream", "webpage_url_domain": "twitch.tv", "id": "40500071752",
          "uploader_id": "Monstercat", "display_id": "monstercat"}
    assert live.site_of(tw) == {"site": "twitch", "video_id": "40500071752",
                                "channel": "monstercat", "play_url": ""}
    # Even with empty metadata, the login name is salvaged from the URL
    assert live.site_of({}, "https://www.twitch.tv/Shroud?x=1")["channel"] == "shroud"
    # chzzk has no embed, so it is the one site that carries a manifest for the
    # page to play. The channel is the hex id, the shape a glossary is keyed by.
    cz = {"extractor_key": "CHZZKLive", "webpage_url_domain": "chzzk.naver.com",
          "id": "c0d9", "channel_id": "c0d9",
          "formats": [{"format_id": "hls-3", "protocol": "m3u8_native", "height": 720,
                       "url": "https://cdn.example/720.m3u8"},
                      {"format_id": "hls-ll-4", "protocol": "m3u8_native", "height": 1080,
                       "url": "https://cdn.example/1080-ll.m3u8"},
                      {"format_id": "hls-4", "protocol": "m3u8_native", "height": 1080,
                       "url": "https://cdn.example/1080.m3u8"}]}
    assert live.site_of(cz) == {"site": "chzzk", "video_id": "c0d9", "channel": "c0d9",
                                "play_url": "https://cdn.example/1080.m3u8"}
    # The tallest plain rendition, not the low-latency one beside it: hls.js is
    # fussier about partial segments than about an ordinary playlist.
    assert live.play_url_of(cz) == "https://cdn.example/1080.m3u8"
    assert live.play_url_of({"formats": [{"format_id": "hls-ll-4", "protocol": "m3u8_native",
                                          "height": 1080, "url": "https://cdn.example/ll.m3u8"}]}) \
        == "https://cdn.example/ll.m3u8"
    assert live.play_url_of({}) == ""
    gen = {"extractor_key": "Generic", "webpage_url_domain": "cdn.example", "id": "master"}
    assert live.site_of(gen, "https://cdn.example/live/master.m3u8") == {
        "site": "other", "video_id": "master", "channel": "", "play_url": ""}
    assert live.looks_like_m3u8("https://cdn.example/a/b.m3u8?tok=1")
    assert not live.looks_like_m3u8("https://www.youtube.com/watch?v=x")


def test_manifest_info_sees_the_end_of_a_recording():
    vod = "data:application/vnd.apple.mpegurl,%23EXTM3U%0A%23EXT-X-TARGETDURATION:10%0A%23EXTINF:9.0,%0Aa.ts%0A%23EXTINF:9.0,%0Ab.ts%0A%23EXT-X-ENDLIST%0A"
    live_ = "data:application/vnd.apple.mpegurl,%23EXTM3U%0A%23EXT-X-TARGETDURATION:2%0A%23EXTINF:2.0,%0Aa.ts%0A"
    assert live.manifest_info(vod)["ended"] is True and live.manifest_info(vod)["window_s"] == 18.0
    assert live.manifest_info(live_)["ended"] is False


def _fake_playlist(monkeypatch, session, pdt="2026-01-01T00:20:00Z", release_ts=None, window_s=600.0):
    """Stand in for `yt-dlp -j` and reading the playlist. The first segment is 1200 s after the stream began, the window is 600 s."""
    import datetime
    import subprocess as sp
    rel = release_ts if release_ts is not None else datetime.datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp()
    meta = json.dumps({"is_live": True, "id": "vid", "title": "t", "extractor_key": "Youtube",
                       "release_timestamp": rel})
    monkeypatch.setattr(live.subprocess, "run",
                        lambda *a, **k: sp.CompletedProcess(args=[], returncode=0, stdout=meta, stderr=""))
    monkeypatch.setattr(live, "resolve_audio",
                        lambda url, youtube=True: ("src", {"pdt": pdt, "window_s": window_s,
                                                           "segments": int(window_s / 2), "target": 2.0}))


def test_user_resume_starts_at_the_live_edge_and_notes_the_gap(session, monkeypatch):
    s = session
    _fake_playlist(monkeypatch, s)
    s.resume_from = 1000.0                    # It stopped at 1000 s; the live edge is now 1200+600=1800 s
    src, idx = s._resolve_hls()
    assert idx == -2 and s._recv_base == 1800.0 and s.gap_s == 800.0   # No rewind -- just note the 800 s that went missing


def test_reconnect_inside_the_session_still_rewinds(session, monkeypatch):
    s = session
    _fake_playlist(monkeypatch, s)
    s.resume_from = 1500.0                    # It broke inside the window (1200~1800)
    s._rewind = True                          # The flag _reconnect raises
    src, idx = s._resolve_hls(reconnect=True)
    assert idx == 150 and s.gap_s == 0.0      # 300 s in = from the 150th 2 s segment, nothing lost


def _chzzk_playlist(monkeypatch, session):
    """A playlist that cannot say where in the broadcast it is.

    chzzk's `timestamp` is *later* than its own PROGRAM-DATE-TIME, so the
    subtraction `media_base_from` makes goes negative and clamps to 0 -- on the
    first reception and on every reattach alike. Returns that clamp so a test
    can show it is the clamp and not a coincidence.
    """
    import datetime
    later = datetime.datetime.fromisoformat("2026-01-01T05:00:00+00:00").timestamp()
    _fake_playlist(monkeypatch, session, release_ts=later)
    monkeypatch.setattr(live.LiveSession, "_spawn_ffmpeg", lambda self, src, idx: None)
    return later


def test_a_reconnect_keeps_the_media_clock_where_reception_reached(session, monkeypatch):
    """A reattach must not put the clock back to the front of the broadcast.

    `continue_base` closed this for a resumed session. A reattach goes through
    the same `_resolve_hls`, which overwrites `_recv_base` with whatever the
    playlist says, and then sets `_recv_s` to 0 -- so on a site whose playlist
    says nothing the session started numbering its lines from zero again, and
    because the panel sorts by time they were wedged in among the lines already
    standing rather than added after them. Switching the video saving is a
    reattach taken on demand, which is how this came back within a minute of
    the button existing.
    """
    s = session
    later = _chzzk_playlist(monkeypatch, s)
    assert live.media_base_from("2026-01-01T00:20:00Z", later, 600.0) == 0.0
    s._recv_base, s._recv_s = 0.0, 264.1      # reception has reached 264.1 s
    assert s._reconnect(1) == "ok"
    assert s._recv_base >= 264.1 and s._recv_s == 0.0
    # A second one does not walk it backwards either -- the guard has to read
    # the clock as it stands now, not as it stood when the session began.
    s._recv_s = 30.0
    assert s._reconnect(2) == "ok"
    assert s._recv_base >= 294.1 and s._recv_s == 0.0


def test_a_reconnect_keeps_the_playlist_clock_when_it_is_the_one_ahead(session, monkeypatch):
    """The control. Where the playlist *can* say, its word is the accurate one.

    YouTube gives a broadcast start time, so PDT minus that keeps counting
    across the break and is already past where reception stopped. `continue_base`
    is a max, so guarding the reattach must leave that case exactly as it was.
    """
    s = session
    _fake_playlist(monkeypatch, s)            # first segment 1200 s in, 600 s window
    monkeypatch.setattr(live.LiveSession, "_spawn_ffmpeg", lambda self, src, idx: None)
    s._recv_base, s._recv_s = 0.0, 1000.0     # the window has moved past where it stopped
    assert s._reconnect(1) == "ok"
    assert s._recv_base == 1200.0             # the playlist's, not the session's 1000


def test_media_offset_separates_a_real_zero_from_no_answer():
    """0.0 and None are different answers and `_resume_point` acts on which.

    A broadcast in its first window really is at 0 and can be rewound into. A
    playlist whose two numbers contradict each other is not at 0, it is unknown,
    and the old clamp spelled both of them 0.
    """
    import datetime
    at = datetime.datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp()
    later = datetime.datetime.fromisoformat("2026-01-01T05:00:00+00:00").timestamp()
    # The playlist's front is the broadcast's start: a real answer, and a rewind
    # measured from it lands where it should.
    assert live.media_offset("2026-01-01T00:00:00Z", at, 0.0) == 0.0
    assert live.media_offset("2026-01-01T00:20:00Z", at, 0.0) == 1200.0
    # Nothing to subtract.
    assert live.media_offset(None, at, 0.0) is None
    assert live.media_offset("2026-01-01T00:20:00Z", None, 0.0) is None
    assert live.media_offset("not a timestamp", at, 0.0) is None
    # chzzk: both numbers are there and they disagree about which came first.
    assert live.media_offset("2026-01-01T00:20:00Z", later, 0.0) is None
    # The clock still spells all of those 0, because `continue_base` is what
    # answers for it there.
    assert live.media_base_from("2026-01-01T00:20:00Z", later, 0.0) == 0.0
    assert live.media_base_from("2026-01-01T00:20:00Z", at, 0.0) == 1200.0


def test_a_playlist_that_cannot_say_where_it_is_does_not_rewind(session, monkeypatch):
    """A rewind needs a measurement, and reading 0 for "cannot say" invents one.

    On the chzzk reattach this was found on the invented measurement put the read
    a window's length behind where reception stopped -- 264 s received, a 600 s
    window, and ffmpeg told to start at segment 132 -- so about 72 s of speech was
    transcribed and published a second time. Joining at the live edge loses the
    seconds across the break instead, which on a site read this way is inherent,
    and says so in the subtitles without quoting a length it did not measure.
    """
    import datetime
    s = session
    later = datetime.datetime.fromisoformat("2026-01-01T05:00:00+00:00").timestamp()
    _fake_playlist(monkeypatch, s, release_ts=later)
    s.resume_from, s._rewind = 264.1, True
    src, idx = s._resolve_hls(reconnect=True)
    assert idx == -2                          # the live edge. It used to be segment 132
    assert s.gap_s == 0.0 and s._gap_unknown is True


def test_a_legitimate_zero_offset_still_rewinds(session, monkeypatch):
    """The control. Every broadcast is at 0 for its first window.

    The discriminator is the playlist's two numbers disagreeing, never the offset
    coming out 0 -- a session that broke a few minutes into a broadcast has a real
    0 for its playlist's front and the whole point is that it rewinds into it.
    """
    s = session
    _fake_playlist(monkeypatch, s, pdt="2026-01-01T00:00:00Z")   # front == broadcast start
    s.resume_from, s._rewind = 300.0, True
    src, idx = s._resolve_hls(reconnect=True)
    assert idx == 150 and s.gap_s == 0.0      # 300 s in = the 150th 2 s segment
    assert s._gap_unknown is False


def _rebase_note(session):
    """The note `_reconnect` sent through the ring, which is where it goes."""
    return [it[2] for it in session._ring._d if it[0] == "rebase"][-1]


def test_an_unmeasured_hole_is_announced_without_a_number(session, monkeypatch):
    """Two failures are being kept apart here, and they pull in opposite directions.

    Saying nothing leaves a hole in the transcript that reads as reception
    quietly going wrong, which is the thing `gap_s` notes exist to prevent.
    Saying "about N s" when N was never measured is worse still, and on this path
    there was a real N to borrow: `gap_s` is not cleared by anything else, so a
    session that resumed with a measured 800 s hole announced that same 800 s
    again as the length of the next break.
    """
    import datetime
    s = session
    monkeypatch.setattr(live.LiveSession, "_spawn_ffmpeg", lambda self, src, idx: None)
    later = datetime.datetime.fromisoformat("2026-01-01T05:00:00+00:00").timestamp()
    _fake_playlist(monkeypatch, s, release_ts=later)
    s._recv_base, s._recv_s = 0.0, 264.1
    s.gap_s = 800.0                           # left over from the resume that started this session
    assert s._reconnect(1) == "ok"
    note = _rebase_note(s)
    assert "800" not in note and " s went unreceived" not in note
    assert "does not say how much" in note and "reception was cut" in note
    # A switch the user asked for says so, the same distinction the measured note makes.
    s._recv_s = 10.0
    assert s._reconnect(2, requested=True) == "ok"
    assert "saving was switched" in _rebase_note(s)


def test_a_window_that_could_not_be_read_does_not_reuse_the_last_measurement(session, monkeypatch):
    """The same borrowed number, reached the other way.

    This branch already said in its own comment that it does not write down what
    it could not measure, and it was leaving `gap_s` standing for `_reconnect` to
    read.
    """
    s = session
    _fake_playlist(monkeypatch, s, window_s=0.0)     # the window could not be read
    s.resume_from, s._rewind = 1500.0, True
    s.gap_s = 800.0
    src, idx = s._resolve_hls(reconnect=True)
    assert idx == -2 and s.gap_s == 0.0 and s._gap_unknown is True


def test_only_a_stood_up_ffmpeg_is_counted_as_an_attach(session, monkeypatch):
    """`attached` is the only thing in the status that moves when a reattach
    finishes, and the screen takes the "switching the video saving" notice down
    on it. So it has to mean one thing: a process is reading now. A reattach that
    could not resolve must leave it alone, or the notice comes down over a
    reception that never came back and the session's own error is the only true
    thing left on screen.
    """
    s = session
    # CI installs numpy, pytest and ruff and nothing else, so there is no ffmpeg
    # on the machine and `read_plan` asking for its path raises. The name is what
    # this test needs, never the program -- Popen is a stub two lines on.
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    monkeypatch.setattr(live.subprocess, "Popen", lambda *a, **k: object())
    assert s.status()["attached"] == 0
    s._spawn_ffmpeg("src", -2)
    assert s.status()["attached"] == 1

    def no_playlist(reconnect=False):
        raise RuntimeError("network")
    s._resolve_hls = no_playlist
    assert s._reconnect(1) == "retry"
    assert s.status()["attached"] == 1
    # A broadcast that has ended does not stand one up either.
    s._resolve_hls = lambda reconnect=False: (None, None)
    assert s._reconnect(2) == "ended"
    assert s.status()["attached"] == 1
    # One that attached does.
    s._resolve_hls = lambda reconnect=False: ("src", -2)
    assert s._reconnect(3) == "ok"
    assert s.status()["attached"] == 2
    # And the status saying so reaches the screen -- no other line on the
    # reattach path writes one, so without it the count moved where nobody
    # could see it.
    assert [e for e in s.emitted if e.get("type") == "status"][-1]["attached"] == 2


def test_multiview_add_resumes_a_stopped_session_as_a_warm_member(monkeypatch):
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)
    store.save_session({"id": "old-9", "state": "stopped", "stopped_by": "user", "url": "https://x/old",
                        "source": "hls", "source_lang": "ja", "viewer_lang": "ko",
                        "backend": "local-m2m100", "asr_backend": "tcpp-lite", "lines": 0,
                        "media_base": 0.0, "audio_s": 5.0, "recv_t": 5.0}, "")
    try:
        a = live._new_session("https://x/a", "ja", "ko", "local-m2m100", "broadcast", "", True, None, "hls", "")
        g = live.multiview_start([{"session": a.id}, {"session": "old-9"}], "ja", "ko", "local-m2m100", focus=a.id)
        old = live.get("old-9")
        assert old is not None and old.group == g["id"] and not old._focus.is_set()
        assert a._focus.is_set() and [m["id"] for m in g["members"]] == [a.id, "old-9"]
        assert not old._rewind and old.resume_from == 5.0
        # A session that does not exist fails, and no bundle is left behind
        assert live.multiview_start([{"session": "nope"}], "ja", "ko", "local-m2m100")["error"]
        assert list(live._groups) == [g["id"]]
    finally:
        live._sessions.clear()
        live._groups.clear()


def test_ffmpeg_is_spawned_without_inheriting_std_handles(session, monkeypatch):
    """A Windows user on 0.3.1 died at `_spawn_ffmpeg` with WinError 6.

    Given no stdin/stderr, `Popen` duplicates the parent's handles to pass them
    down, and in a process that started without sound standard handles that
    duplication fails.
    """
    seen = {}
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    monkeypatch.setattr(live.subprocess, "Popen",
                        lambda cmd, **kw: seen.update(cmd=cmd, kw=kw) or object())
    session._spawn_ffmpeg("https://example.invalid/live.m3u8", -2)
    assert "-nostdin" in seen["cmd"]
    assert seen["kw"]["stdout"] is subprocess.PIPE
    assert seen["kw"]["stdin"] == subprocess.DEVNULL   # Do not lean on an inherited handle
    assert "stderr" in seen["kw"]


def _probe(stdout: str, code: int = 0):
    return lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=code,
                                                       stdout=stdout, stderr="")


LIVE_META = json.dumps({"title": "what yt-dlp fetched", "id": "v1", "is_live": True})


def test_resolving_the_url_again_does_not_blank_a_title(session, monkeypatch):
    """An unparseable probe used to leave the session with no name at all.

    A playlist address makes `yt-dlp -j` exit 0 with output that is not JSON, so
    the metadata was `{}` and the title became "" -- the session lost the name it
    was listed under, on a reconnect that was otherwise fine.
    """
    monkeypatch.setattr(live.subprocess, "run", _probe("not json at all"))
    monkeypatch.setattr(live, "resolve_audio", lambda url, youtube=True: ("http://x/a.m3u8", {}))
    session.title = "the name it had"
    session._resolve_hls()
    assert session.title == "the name it had"


def test_a_rename_outlives_resolving_the_url_again(session, monkeypatch):
    """yt-dlp's title is a guess we can improve on, never a correction."""
    monkeypatch.setattr(live.subprocess, "run", _probe(LIVE_META))
    monkeypatch.setattr(live, "resolve_audio", lambda url, youtube=True: ("http://x/a.m3u8", {}))
    # Nobody has named it, so the fetched title is an improvement and lands.
    session.title = ""
    session._resolve_hls()
    assert session.title == "what yt-dlp fetched"
    # Once a person has named it, nothing writes over that.
    live._sessions[session.id] = session
    try:
        assert live.set_title(session.id, "the name I typed")["ok"]
    finally:
        live._sessions.pop(session.id, None)
    assert session.title_by_user is True
    session._resolve_hls()
    assert session.title == "the name I typed"
    assert session.status()["title_by_user"] is True


def test_resume_carries_the_rename(monkeypatch):
    """The mark has to survive a restart -- resume re-resolves the URL right after."""
    st = {"id": "title-1", "state": "stopped", "stopped_by": "user", "url": "https://x/live",
          "source": "hls", "source_lang": "ja", "viewer_lang": "ko", "backend": "local-m2m100",
          "asr_backend": "tcpp-lite", "title": "the name I typed", "title_by_user": True,
          "media_base": 0.0, "audio_s": 0.0, "lines": 0}
    store.save_session(st, "")
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)
    assert live.resume("title-1")["resumed"]
    s = live.get("title-1")
    assert (s.title, s.title_by_user) == ("the name I typed", True)
    live._sessions.clear()


def test_auto_detect_language_gets_translated(session):
    """A live session with lang=None (auto-detect) should get translations.

    The ASR returns detected language in result.language. The cue should carry
    that language so _translate() can find src != tgt and translate.
    """
    s = session
    s.lang = None  # auto-detect
    s.viewer_lang = "ko"

    # The pairs land in `calls` from translate(), not from should_translate():
    # recording the question rather than the answer proves the translator was
    # asked, which is not what this test is named after.
    calls = []
    class MockTranslator:
        name = "mock"
        def should_translate(self, text, src, tgt):
            return src != tgt and src != ""
        def translate(self, text, src, tgt, context=None):
            calls.append((text, src, tgt))
            return "T:" + text
    s._tr = MockTranslator()

    # Simulate ASR returning detected language
    s.publish_line("final", "こんにちは", "ja", "")
    s.audio_s = 2.0
    s.publish_line("final", "元気ですか", "ja", "")

    # Wait for translations
    import time
    deadline = time.time() + 2.0
    while time.time() < deadline and len(calls) < 2:
        time.sleep(0.01)

    assert len(calls) == 2
    for text, src, tgt in calls:
        assert src == "ja"
        assert tgt == "ko"
        assert text in ("こんにちは", "元気ですか")
    # And the translations actually reached the screen.
    assert [t for _, t in _translations(s)] == ["T:こんにちは", "T:元気ですか"]


def test_clear_cues_keeps_the_id_space(session):
    """Clearing empties the record but does not hand the same ids out again.

    The event log still holds the cleared lines under the ids they were given,
    and a client that reconnects with `Last-Event-ID` replays them. Restarting
    `_seq` at zero would put the replayed lines and the new ones under one id
    each.
    """
    s = session
    live._sessions[s.id] = s
    try:
        for n in range(3):
            s.audio_s = n * 1.0
            s.publish_line("final", f"line {n}", "ja", "")
        assert len(store.cues(s.id)) == 3
        seq_before = s._seq

        assert live.clear_cues(s.id) == {"ok": True, "cleared": True}

        assert store.cues(s.id) == []
        assert list(s._recent) == [] and s._text_of == {}
        assert (s.lines, s.translated) == (0, 0)
        assert s._seq == seq_before          # not reset
        assert any(e.get("type") == "clear" for e in s.emitted)

        # The next line comes after the cleared ones, not on top of them.
        s.publish_line("final", "after", "ja", "")
        assert store.cues(s.id)[0]["id"] > seq_before
    finally:
        live._sessions.clear()


def test_clear_cues_on_a_session_that_is_gone(isolated):
    assert live.clear_cues("nope") == {"error": "no such live session"}


def test_a_resumed_clock_never_starts_before_where_it_stopped():
    """The playlist's clock is not always one that survives a break.

    chzzk gives no broadcast start time worth the name, so `media_base_from`
    clamps to 0 every time it is asked. A session resumed on that would number
    its lines from zero again and, since the panel sorts by time, wedge them in
    among the lines already there instead of after them.
    """
    # A first reception keeps whatever the playlist said, whatever that is.
    assert live.continue_base(0.0, 0.0) == 0.0
    assert live.continue_base(931.0, 0.0) == 931.0
    # A resume where the playlist has no usable clock (chzzk) continues from the
    # session's own record instead.
    assert live.continue_base(0.0, 264.1) == 264.1
    # A resume where it does (YouTube: PDT minus the broadcast start keeps
    # counting across the break) keeps the playlist's, which is the more accurate
    # of the two and is already past where it stopped.
    assert live.continue_base(931.0, 264.1) == 931.0
    # And it is never dragged backwards by a stale record.
    assert live.continue_base(931.0, 2000.0) == 2000.0


# ---- The session's own audio: carrying the tail and writing the WAV --------------
# hayamimi, which this ingest path was ported from, kept no audio, and a meeting
# transcribed with it on 2026-09-11 could never be transcribed again at better
# quality because the sound was gone. These tests pin the two things that turned
# out to be load-bearing for a record: every uploaded sample reaches the
# transcriber, and every uploaded sample reaches the file.

def _pcm(n_samples: int) -> bytes:
    """Distinguishable int16 PCM -- a ramp, so a dropped stretch shows up as a
    gap in the values rather than as more silence among silence."""
    return (np.arange(n_samples, dtype=np.int32) % 1000).astype(np.int16).tobytes()


def test_feed_carries_the_misaligned_tail_instead_of_dropping_it():
    """An upload is a multiple of the worklet's 128 samples, not of the VAD's 1600.

    The loop used to be `range(0, len(raw) - need + 1, need)`, so the remainder
    was dropped and never made up: two uploads of 2.5 and 1.1 chunks yielded
    two chunks instead of three, silently. Three is the whole of what arrived.
    """
    s = live.LiveSession("", None, "ko", "local-m2m100", source="tab", title="t")
    s._tr = None
    first = s.feed(_pcm(live.CHUNK * 2 + 500))     # 2 chunks + a 500-sample tail
    assert first["ok"] and abs(first["queued_s"] - 0.2) < 1e-9
    second = s.feed(_pcm(live.CHUNK - 500))        # the tail completes the third
    assert abs(second["queued_s"] - 0.3) < 1e-9    # 3 chunks, not 2
    assert abs(s._recv_s - 0.3) < 1e-9


def test_mic_session_is_pushed_and_records_every_sample():
    """A mic session takes uploaded audio like a tab one, and writes it down.

    The recordings directory is a tmpdir already: conftest's `isolated` fixture
    redirects it for every test, because a session asked to record does so as
    soon as it is fed and not only when the test is about recording.
    """
    s = live.LiveSession("", None, "ko", "local-m2m100", source="mic", title="m",
                         record=True)
    s._tr = None
    assert s.pushed and s.source == "mic"
    blocks = [_pcm(live.CHUNK * 2 + 500), _pcm(live.CHUNK - 500), _pcm(777)]
    for b in blocks:
        assert s.feed(b)["ok"]
    assert s.status()["recording"] and not s.status()["recording_error"]
    s.stop()
    written = b"".join(blocks)
    with wave.open(s._wav_path, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        assert w.getframerate() == live.SAMPLE_RATE
        assert w.readframes(w.getnframes()) == written
    live._sessions.clear()


def test_recording_failure_leaves_the_transcription_running(monkeypatch):
    """A full disk costs the recording, never the subtitles already on screen.

    The error has to be visible, though: a recording that stopped with nobody
    told is the failure this feature exists to prevent, moved one level down.
    """
    monkeypatch.setattr(live.wave, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("no space left")))
    s = live.LiveSession("", None, "ko", "local-m2m100", source="mic", title="m",
                         record=True)
    s._tr = None
    r = s.feed(_pcm(live.CHUNK * 2))
    assert r["ok"] and abs(r["queued_s"] - 0.2) < 1e-9      # audio still queued
    assert "no space left" in s.status()["recording_error"]
    assert s.status()["recording_s"] == 0
    live._sessions.clear()


def test_stop_flushes_the_carried_tail_so_the_last_words_are_transcribed():
    """The fragment shorter than one chunk is zero-padded, not discarded -- the
    end of a meeting is where the closing agreement is."""
    s = live.LiveSession("", None, "ko", "local-m2m100", source="tab", title="t")
    s._tr = None
    s.feed(_pcm(500))                      # nothing reaches the ring yet
    assert abs(s._recv_s) < 1e-9
    s.stop()
    assert abs(s._recv_s - live.FRAME_S) < 1e-9   # one padded chunk did


# ---- Saving a broadcast the server pulls ----------------------------------------
# The recorder was written for the pushed sources and called from feed() alone, so
# an hls session -- the one kind the user cannot re-capture from their own machine
# -- wrote nothing at all. What these pin is the seam it is now called across: the
# transcriber takes whole VAD chunks and the file takes every byte that arrived,
# and those are not the same set of bytes.

class _FakeStdout:
    """ffmpeg's stdout. `read(n)` short-reads at the end, like a real pipe that has
    been closed -- which is the read the file and the transcriber disagree about."""

    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def read(self, n: int) -> bytes:
        out = self.data[self.pos:self.pos + n]
        self.pos += len(out)
        return out


class _FakeFF:
    def __init__(self, data: bytes):
        self.stdout = _FakeStdout(data)

    def terminate(self):
        pass

    def kill(self):
        pass


def _hls(record: bool = False) -> live.LiveSession:
    s = live.LiveSession("https://example.invalid/live", "ja", "ko", "local-m2m100",
                         record=record)
    s._tr = None
    return s


def _read(s: live.LiveSession, data: bytes) -> str:
    """Run the reading thread over `data` right here and report what the transcribing
    side took out of the ring ("S" a chunk, "N" a marker). No ffmpeg is spawned and
    nothing goes near the network: `_resolve_hls` says the broadcast is over, so
    there is no reconnect either."""
    s._ff = _FakeFF(data)
    s._resolve_hls = lambda reconnect=False: (None, None)
    s._stop.wait = lambda t: False
    s._read_loop()
    s._close_recording()
    return "".join("S" if isinstance(c, np.ndarray) else "N" for c in s._consume())


def test_an_hls_session_records_the_tail_the_transcriber_has_to_drop():
    """Every byte ffmpeg produced reaches the file, including the short final read.

    The transcriber cannot take that read -- it is less than one VAD chunk, and the
    loop breaks on it -- but it is still audio that exists nowhere else, and the
    whole reason for keeping a file is that it cannot be made again.
    """
    data = _pcm(live.CHUNK * 2 + 320)          # two whole chunks and 0.02 s more
    s = _hls(record=True)
    assert _read(s, data) == "SSN"             # the transcriber saw the two chunks only
    with wave.open(s._wav_path, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        assert w.getframerate() == live.SAMPLE_RATE
        assert w.getnframes() == len(data) // 2
        assert w.readframes(w.getnframes()) == data
    live._sessions.clear()


def test_an_hls_session_not_asked_to_record_writes_nothing():
    """Unasked is off, and off means no file at all -- not an empty one. Seven
    hours is about 800 MB (16000 Hz x 2 bytes = 32 KB/s), and multiview runs up to
    four of them at once."""
    s = _hls()
    assert s.record is False
    assert _read(s, _pcm(live.CHUNK * 2)) == "SSN"
    assert s.status()["recording"] == "" and s._wav is None
    d = live.paths.recordings_dir()
    assert not os.path.isdir(d) or os.listdir(d) == []


def test_a_block_that_splits_a_sample_keeps_every_byte_and_whole_frames():
    """`_record` takes blocks of any length now, so one can stop half way through a
    16-bit sample. The half leads the next block.

    It is not that writing it through corrupts the file -- `wave` concatenates the
    bytes and the file reads back byte for byte either way. It is that the half
    leaves `_wav_s` a fraction of a frame out, and `_wav_s` is the `recording_s`
    the status publishes, so the length on screen stops being a count of samples.
    What must not change is the bytes: nothing dropped, nothing written twice.
    """
    s = _hls(record=True)
    data = _pcm(2000)                          # 4000 bytes
    s._record(data[:1501])                     # ends mid-sample
    assert s._wav_s * live.SAMPLE_RATE == 750  # whole samples, not 750.5
    s._record(data[1501:])
    s._close_recording()
    with wave.open(s._wav_path, "rb") as w:
        assert w.readframes(w.getnframes()) == data
    live._sessions.clear()


def test_a_block_of_nothing_but_half_a_sample_opens_no_file():
    """An empty recording with a path in the status reads as a recording that is
    working, which is the one thing the status must never say."""
    s = _hls(record=True)
    s._record(_pcm(1)[:1])
    assert s._wav is None and s.status()["recording"] == ""
    live._sessions.clear()


def test_nothing_is_recorded_unless_it_is_asked_for():
    """Off for every source alike, and being asked is the only way on.

    The pushed sources used to default to on, and the page sent the field only
    when the box was ticked -- so an unticked box left that default standing and
    a microphone session recorded with no way to stop it. Saving the audio is a
    choice the user makes, which means it has to be one they can unmake.
    """
    assert _hls().record is False
    assert live.LiveSession("", None, "ko", "local-m2m100", source="mic").record is False
    assert live.LiveSession("", None, "ko", "local-m2m100", source="tab").record is False
    assert _hls(record=True).record is True
    assert live.LiveSession("", None, "ko", "local-m2m100", source="mic",
                            record=True).record is True


def test_a_pushed_session_not_asked_to_record_writes_nothing():
    """The one the old default made unstoppable. Fed a full VAD chunk and stopped,
    a microphone session that was not asked to record leaves no file -- not an
    empty one, and not one the user has to go and delete."""
    s = live.LiveSession("", None, "ko", "local-m2m100", source="mic", title="m",
                         record=False)
    s._tr = None
    assert s.feed(_pcm(live.CHUNK * 2))["ok"]
    s.stop()
    assert s.status()["recording"] == "" and s._wav is None
    d = live.paths.recordings_dir()
    assert not os.path.isdir(d) or os.listdir(d) == []
    live._sessions.clear()


def test_resume_carries_the_recording_flag_back(monkeypatch):
    """A resumed session records if the session it continues did.

    The flag has to be read back off the stored status; a resume that forgets it
    would go on transcribing with the recording silently stopped, which is the
    failure the recording exists to prevent. A record saved before the flag existed
    has no key, and off is the right reading of that -- the same rule as a fresh
    start, where an unticked box is what a missing field means.
    """
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)
    base = {"state": "stopped", "stopped_by": "user", "url": "https://x/live",
            "source": "hls", "source_lang": "ja", "viewer_lang": "ko",
            "backend": "local-m2m100", "asr_backend": "tcpp-lite",
            "media_base": 0.0, "audio_s": 5.0, "lines": 0}
    store.save_session({**base, "id": "rec-1", "record": True}, "")
    assert live.resume("rec-1")["resumed"]
    assert live.get("rec-1").record is True
    live._sessions.clear()

    store.save_session({**base, "id": "rec-2", "record": False}, "")
    assert live.resume("rec-2")["resumed"]
    assert live.get("rec-2").record is False
    live._sessions.clear()

    store.save_session({**base, "id": "rec-3"}, "")            # stored before the flag
    assert live.resume("rec-3")["resumed"]
    assert live.get("rec-3").record is False                   # no key, so not asked
    live._sessions.clear()


# ---- Saving the video of a broadcast the server pulls ----------------------------
# The broadcast is fetched once and fanned out: one ffmpeg, the sound down the pipe
# to the VAD and the picture into an mp4. How many inputs that takes is the site's
# answer, not ours -- a YouTube live broadcast has no single file with both streams
# in it, so the picture comes in as a second input. Nothing is spawned here --
# `read_plan` is the seam the argv can be read across without an ffmpeg anywhere
# near it, the same way `burn.plan` can be read.

def _plan(monkeypatch, src, start_index, out="", video_src=""):
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    return live.read_plan(src, start_index, out, video_src)


def test_not_saving_the_video_leaves_the_reading_command_as_it_was(monkeypatch):
    """A session that was not asked for the video must be paying nothing for the
    feature -- not a map, not an output, not a byte more fetched. This is the
    command mimiwatch has always run, written out in full so that a change to it
    has to be meant."""
    assert _plan(monkeypatch, "https://example.invalid/live.m3u8", -2) == [
        "ffmpeg", "-loglevel", "error", "-nostdin",
        "-live_start_index", "-2", "-i", "https://example.invalid/live.m3u8",
        "-vn", "-ac", "1", "-ar", str(live.SAMPLE_RATE), "-f", "s16le", "-",
    ]


def test_saving_the_video_adds_an_input_rather_than_a_second_fetch(monkeypatch):
    """Two inputs, two outputs, one process -- and the sound is the rendition
    transcription would have read anyway.

    A YouTube live broadcast has no single file with both streams in it (the
    measurement is in `resolve_video`), so `-f best` failed every time and the
    feature never worked there. The picture comes in as its own input instead,
    and because the sound is still the audio-only rendition, **the transcription
    side does not change at all when the box is ticked**: the same samples reach
    the VAD and the same WAV is written. A second ffmpeg pulling the picture
    would have fetched the broadcast twice over instead.
    """
    cmd = _plan(monkeypatch, "https://example.invalid/audio.m3u8", -2, "/tmp/out.mp4",
                "https://example.invalid/video.m3u8")
    assert cmd == [
        "ffmpeg", "-loglevel", "error", "-nostdin", "-y",
        "-live_start_index", "-2", "-i", "https://example.invalid/video.m3u8",
        "-live_start_index", "-2", "-i", "https://example.invalid/audio.m3u8",
        "-map", "1:a:0",
        "-vn", "-ac", "1", "-ar", str(live.SAMPLE_RATE), "-f", "s16le", "-",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac",
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "/tmp/out.mp4",
    ]
    assert cmd.count("-i") == 2
    # The sound ffmpeg transcribes from is the audio address, whichever input it
    # sits on -- that is the whole of what "an added input, not a changed one"
    # means, and it is the line that would break if the two were ever swapped.
    assert cmd[cmd.index("-map") - 1] == "https://example.invalid/audio.m3u8"


def test_one_muxed_address_is_still_one_input(monkeypatch):
    """A site that hands back a single file with both streams in it -- every
    format of a live chzzk channel is that shape, and none of them is audio-only
    -- must still work, and with one input rather than the same address opened
    twice. There the sound does come out of the muxed rendition, as it always
    did."""
    cmd = _plan(monkeypatch, "https://example.invalid/muxed.m3u8", -2, "/tmp/out.mp4",
                "https://example.invalid/muxed.m3u8")
    assert cmd == [
        "ffmpeg", "-loglevel", "error", "-nostdin", "-y",
        "-live_start_index", "-2", "-i", "https://example.invalid/muxed.m3u8",
        "-map", "0:a:0",
        "-vn", "-ac", "1", "-ar", str(live.SAMPLE_RATE), "-f", "s16le", "-",
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "copy", "-c:a", "aac",
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "/tmp/out.mp4",
    ]
    assert cmd.count("-i") == 1


def test_each_output_names_the_stream_it_wants(monkeypatch):
    """A bare `-map 0:a` is not good enough and the difference is a session with
    no subtitles at all.

    A master playlist opens as one input carrying every variant, and against one
    of those ffmpeg found five audio streams and died with *s16le muxer does not
    support more than one stream of type audio*, having delivered 0 bytes to the
    pipe. `-g` normally hands back a single variant so it rarely bites, but
    naming the first audio and the first video stream is correct and costs
    nothing.
    """
    cmd = _plan(monkeypatch, "https://example.invalid/muxed.m3u8", -2, "/tmp/out.mp4")
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"] == \
        ["0:a:0", "0:v:0", "0:a:0"]
    assert "0:a" not in cmd and "0:v" not in cmd
    # With two inputs the maps name the input as well, and the picture is input 0
    # so that the mp4's video map reads the same in both shapes.
    two = _plan(monkeypatch, "https://example.invalid/audio.m3u8", -2, "/tmp/out.mp4",
                "https://example.invalid/video.m3u8")
    assert [two[i + 1] for i, a in enumerate(two) if a == "-map"] == \
        ["1:a:0", "0:v:0", "1:a:0"]


def test_the_picture_is_copied_and_only_the_sound_re_encoded(monkeypatch):
    """The picture must never go through an encoder.

    Re-encoding it would put the whole of a GPU-less machine's CPU behind
    something it is already paying for once in transcription; what this feature
    costs is disk. The audio is the exception and has to be, because HLS hands
    over ADTS AAC that mp4 will not take -- `read_plan`'s docstring has why the
    bitstream filter is not the answer.
    """
    cmd = _plan(monkeypatch, "https://example.invalid/muxed.m3u8", -2, "/tmp/out.mp4")
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "libx264" not in cmd
    # The pcm output still throws the pixels away; only the mp4 keeps them.
    assert "-vn" in cmd and cmd[-1] == "/tmp/out.mp4"


def test_a_part_is_a_fragmented_mp4(monkeypatch):
    """A killed process must still leave a file that plays.

    A plain mp4 writes its index when the process ends, so `kill -9` -- how a
    program people close ends most of the time -- would leave nothing a player
    opens. Fragmented, the index is written along the way and a file cut off in
    the middle plays up to its last complete fragment.
    """
    cmd = _plan(monkeypatch, "https://example.invalid/muxed.m3u8", -2, "/tmp/out.mp4")
    flags = cmd[cmd.index("-movflags") + 1]
    for f in ("frag_keyframe", "empty_moov", "default_base_moof"):
        assert f in flags


def test_the_next_video_part_is_numbered_beside_the_first():
    """The signed URL expires and reception breaks, so one broadcast can leave
    several files. They are numbered rather than stamped afresh, so they sort
    together and read in order -- `burn.out_path_for` numbers a re-burn the same
    way."""
    d = live.paths.recordings_dir()
    os.makedirs(d, exist_ok=True)
    first = live.part_path("20260915-120000-abc")
    assert first == os.path.join(d, "20260915-120000-abc.mp4")
    open(first, "wb").close()
    second = live.part_path("20260915-120000-abc")
    assert second == os.path.join(d, "20260915-120000-abc (2).mp4")
    open(second, "wb").close()
    assert live.part_path("20260915-120000-abc") == \
        os.path.join(d, "20260915-120000-abc (3).mp4")


def test_a_session_not_asked_for_video_opens_no_part(monkeypatch):
    """Off is the default, and off puts nothing on disk and nothing in the argv
    -- not even the directory the parts would go in."""
    monkeypatch.setattr(live, "resolve_video",
                        lambda url: pytest.fail("resolve_video was called"))
    s = _hls()
    assert s.record_video is False and s._saving_video() is False
    assert s._next_video_part() == ""
    assert s.status()["recording_video"] == ""
    assert s.status()["recording_video_bytes"] == 0
    d = live.paths.recordings_dir()
    assert not os.path.isdir(d) or os.listdir(d) == []


def test_a_pushed_source_has_no_video_to_save():
    """The extension and the capture page upload sound and nothing else, so the
    flag is dropped rather than carried as a promise the session cannot keep. The
    page hides the box for those sources; this is the other half of it."""
    for src in ("tab", "mic"):
        s = live.LiveSession("", None, "ko", "local-m2m100", source=src, title="t",
                             record_video=True)
        assert s.record_video is False and s._saving_video() is False
        assert s.status()["record_video"] is False
    assert live.LiveSession("https://example.invalid/live", "ja", "ko",
                            "local-m2m100", record_video=True).record_video is True


def test_resolve_video_hands_back_the_picture_and_the_sound(monkeypatch):
    """Two addresses, because on a live YouTube broadcast there is no one file
    with both streams in it.

    `-g` prints the selected formats in the order the selector named them, so
    the picture is the first line and the sound the second (measured against a
    live broadcast: line 0 was itag 270, line 1 itag 234). The media time is
    read off the *audio* manifest -- that is the playlist the transcription
    input reads, and every timing behaviour around it was measured there.
    """
    monkeypatch.setattr(live.subprocess, "run",
                        _probe("http://x/video.m3u8\nhttp://x/audio.m3u8\n"))
    monkeypatch.setattr(live, "manifest_info", lambda u: {"read": u})
    assert live.resolve_video("https://example.invalid/live") == \
        ("http://x/video.m3u8", "http://x/audio.m3u8", {"read": "http://x/audio.m3u8"})


def test_one_address_back_stands_in_for_both(monkeypatch):
    """A site whose formats all carry both streams answers the pair selector
    with a single file, and that is a working answer rather than a failure --
    every format of a live chzzk channel is that shape (measured: 10 formats,
    not one of them audio-only). The same address for both is what `read_plan`
    reads as one input."""
    monkeypatch.setattr(live.subprocess, "run", _probe("http://x/muxed.m3u8\n"))
    monkeypatch.setattr(live, "manifest_info", lambda u: {})
    video, audio, _ = live.resolve_video("https://example.invalid/live")
    assert video == audio == "http://x/muxed.m3u8"


def test_the_picture_is_resolved_beside_the_sound_not_instead_of_it(session,
                                                                    monkeypatch):
    """What the session does with the two addresses, end to end: the sound is
    what `_resolve_hls` returns and therefore what transcription reads, the
    picture is kept for the argv, and `_spawn_ffmpeg` puts them together.

    The old code resolved one address for both and handed it to transcription,
    so ticking the box changed the rendition the VAD heard. It does not any
    more, and this is the line that says so.
    """
    s = session
    s.record_video = True
    monkeypatch.setattr(live.subprocess, "run", _probe(LIVE_META))
    monkeypatch.setattr(live, "resolve_video",
                        lambda url: ("http://x/video.m3u8", "http://x/audio.m3u8", {}))
    monkeypatch.setattr(live, "resolve_audio",
                        lambda url, youtube=True: pytest.fail("resolved the sound twice"))
    src, idx = s._resolve_hls()
    assert src == "http://x/audio.m3u8" and s._vid_src == "http://x/video.m3u8"

    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    cmds = []
    monkeypatch.setattr(live.subprocess, "Popen",
                        lambda cmd, **kw: cmds.append(cmd) or _FakeFF(b""))
    s._spawn_ffmpeg(src, idx)
    assert cmds[-1] == live.read_plan("http://x/audio.m3u8", -2, s._vid_path,
                                      "http://x/video.m3u8")
    assert cmds[-1].count("-i") == 2


def test_a_video_rendition_that_cannot_be_resolved_costs_only_the_video(session,
                                                                        monkeypatch):
    """Losing the subtitles because the picture was unavailable is the wrong
    trade.

    `resolve_video` failing where `resolve_audio` would have worked is a
    plausible thing for a site to do -- a rendition behind a login, a format list
    that came back with nothing playable in it -- and the session goes on with
    the audio-only rendition it would have resolved anyway, with no second input
    left behind in the argv. Once it has given up, it stays given up: retrying
    every reconnect would spend a yt-dlp call per break on something that has
    already been answered.
    """
    s = session
    s.record_video = True
    monkeypatch.setattr(live.subprocess, "run", _probe(LIVE_META))
    monkeypatch.setattr(live, "resolve_audio",
                        lambda url, youtube=True: ("http://x/audio.m3u8", {}))

    def no_muxed(url):
        raise RuntimeError("yt-dlp found no rendition with the picture in it")
    monkeypatch.setattr(live, "resolve_video", no_muxed)

    src, idx = s._resolve_hls()
    assert src == "http://x/audio.m3u8" and idx == -2
    assert s.state != "error" and s.error is None
    assert "no rendition with the picture" in s.status()["recording_video_error"]
    assert s._saving_video() is False
    assert s._next_video_part() == "" and s._vid_src == ""


def test_parts_that_die_at_once_drop_the_video_and_not_the_subtitles(
        session, fake_ffmpeg, monkeypatch):
    """One process means a failing mp4 output takes reception down with it.

    The read loop reaps and reattaches, so the session recovers by itself -- but
    left alone it would recover into the same failure for as long as the
    broadcast lasts, once per break, each one costing the subtitles a reconnect.
    So a part that ended within `VIDEO_PART_OK_S` counts as a bad one, and after
    `VIDEO_RECONNECT_TRIES` of them the video is given up on for the rest of the
    session: the reason goes in the status, and every reconnect after that
    resolves audio-only and carries on with the subtitles.
    """
    s = session
    s.record_video = True
    # The reconnect count must not be what ends this run -- the video giving up
    # is what is being watched, and reception is meant to survive it.
    monkeypatch.setattr(live, "HLS_RECONNECT_TRIES", 50)
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    cmds = []

    def fake_popen(cmd, **kw):
        cmds.append(cmd)
        if cmd[-1].endswith(".mp4"):
            # ffmpeg makes its output file as it starts, and that is what
            # `part_path` reads to find the next free number.
            open(cmd[-1], "wb").close()
        return fake_ffmpeg(1)          # one chunk, then the pipe closes at once
    monkeypatch.setattr(live.subprocess, "Popen", fake_popen)
    reconnects = []

    def fake_resolve(reconnect=False):
        reconnects.append(reconnect)
        # Seven breaks the session reattaches over, then the broadcast is over.
        return ("src", -2) if len(reconnects) <= 7 else (None, None)
    s._resolve_hls = fake_resolve
    s._stop.wait = lambda t: False

    s._spawn_ffmpeg("src", -2)         # the first part, as _start_reader opens it
    s._read_loop()

    # Five parts died on the spot and the sixth ffmpeg was stood up without an
    # mp4 output at all.
    assert len(s._vid_parts) == live.VIDEO_RECONNECT_TRIES
    assert len(set(s._vid_parts)) == len(s._vid_parts)      # each break, a new part
    assert all("-f" in c and "mp4" in c for c in cmds[:live.VIDEO_RECONNECT_TRIES])
    assert all("mp4" not in c for c in cmds[live.VIDEO_RECONNECT_TRIES:])
    assert cmds[-1] == live.read_plan("src", -2)            # back to what it was
    # The reason is on the status, and the last part written is still named there.
    assert str(live.VIDEO_RECONNECT_TRIES) in s.status()["recording_video_error"]
    assert s.status()["recording_video"] == s._vid_parts[-1]
    # And the subtitles ran the whole way through: a chunk out of every one of
    # the eight ffmpegs, and the session ended because the broadcast did.
    seq = "".join("S" if isinstance(c, np.ndarray) else "N" for c in s._consume())
    assert seq.count("S") == len(cmds) == 8
    assert s.stopped_by == "ended" and s.state == "stopped"


def test_a_part_that_ran_long_enough_clears_the_count(session):
    """A part ending is the normal case, not a fault: the muxed URL is signed and
    expires, and reception breaks. Only parts that die on the spot say the mp4
    output itself is the problem, so one that ran resets the count -- the same
    rule the read loop applies when sound arrives."""
    s = session
    s.record_video = True
    s._vid_bad = live.VIDEO_RECONNECT_TRIES - 1
    s._vid_began = time.time() - live.VIDEO_PART_OK_S - 1
    s._video_part_ended()
    assert s._vid_bad == 0 and s._vid_error == "" and s._saving_video() is True
    # And a part that did not last is counted, once, off the same clock.
    s._vid_began = time.time()
    s._video_part_ended()
    assert s._vid_bad == 1 and s._vid_error == ""


def test_the_last_part_of_a_broadcast_that_ended_is_never_counted(monkeypatch):
    """The last part of every recording ends within a moment of the broadcast
    ending, and counting that one would put a failure on the status of a session
    that did exactly what was asked.

    What keeps it out is where the judging happens: a part is judged in
    `_spawn_ffmpeg`, so a session with nothing left to reattach to never judges
    its last one. Doing it where the old ffmpeg ended looked equivalent and was
    not -- that point comes one step *before* the reconnect that discovers the
    broadcast is over, so a guard on "the session is stopping" could not fire
    there, and this is the case it was written for.
    """
    monkeypatch.setattr(live.subprocess, "Popen",
                        lambda cmd, **kw: pytest.fail("stood another ffmpeg up"))
    s = _hls()
    s.record_video = True
    s._vid_bad = live.VIDEO_RECONNECT_TRIES - 1      # one away from giving up
    s._vid_began = time.time()                       # and this part is brand new
    assert _read(s, _pcm(live.CHUNK * 2)) == "SSN"   # the broadcast ends here
    assert s.stopped_by == "ended"
    assert s._vid_bad == live.VIDEO_RECONNECT_TRIES - 1 and s._vid_error == ""


def test_giving_up_takes_effect_before_the_next_part_is_named(monkeypatch):
    """The part that ended is judged where the next ffmpeg is stood up, which is
    what keeps a session that has just given up from opening one more file it is
    about to abandon."""
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    cmds = []
    monkeypatch.setattr(live.subprocess, "Popen",
                        lambda cmd, **kw: cmds.append(cmd) or _FakeFF(b""))
    s = _hls()
    s.record_video = True
    s._vid_bad = live.VIDEO_RECONNECT_TRIES - 1
    s._vid_began = time.time()                       # a part that did not last
    s._spawn_ffmpeg("src", -2)
    assert s._vid_error and cmds[-1] == live.read_plan("src", -2)
    assert s._vid_parts == []                        # and no file was opened for it


def test_a_recordings_directory_that_cannot_be_made_costs_only_the_video(monkeypatch):
    """The rule `_record` keeps for the WAV, kept here too: a recording that
    cannot be written must not take the subtitles with it.

    The directory is made on the way to every part, and `_reconnect` calls
    `_spawn_ffmpeg` outside its own try -- so an OSError there (a read-only
    volume, a permission change, a file sitting on the path) came out of the
    reading thread and ended the session, losing the subtitles over a directory.
    """
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    cmds = []
    monkeypatch.setattr(live.subprocess, "Popen",
                        lambda cmd, **kw: cmds.append(cmd) or _FakeFF(b""))

    def no_directory(*a, **k):
        raise PermissionError("read-only file system")
    monkeypatch.setattr(live.os, "makedirs", no_directory)
    s = _hls()
    s.record_video = True
    s._spawn_ffmpeg("src", -2)                       # must not raise
    assert cmds[-1] == live.read_plan("src", -2)
    assert "read-only" in s.status()["recording_video_error"]
    assert s._saving_video() is False


def test_a_part_ffmpeg_never_opened_is_not_counted_twice(monkeypatch):
    """ffmpeg creates its output file as it starts, so an input it cannot open
    leaves no file at all -- and `part_path` reads the filesystem, so the
    next part is handed back the same name.

    Reusing it is right: there is nothing there to overwrite. Listing it twice
    is not. `_video_bytes` adds the parts up by name, so one file counted once
    per attempt would report a recording several times the size of what is on
    disk, and the closing log would claim files nobody can find.
    """
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    monkeypatch.setattr(live.subprocess, "Popen",
                        lambda cmd, **kw: _FakeFF(b""))      # and writes nothing
    s = _hls()
    s.record_video = True
    s._spawn_ffmpeg("src", -2)
    first = s._vid_path
    s._spawn_ffmpeg("src", -2)
    assert s._vid_path == first                      # the same free name comes back
    assert s._vid_parts == [first]
    assert s._video_bytes() == 0


def test_resume_carries_the_video_flag_back(monkeypatch):
    """A resume that forgets the flag would go on transcribing with the recording
    quietly stopped -- the audio flag needed exactly this line, and so does this
    one. A session stored before the flag existed has no key, which reads as not
    asked."""
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)
    base = {"state": "stopped", "stopped_by": "user", "url": "https://x/live",
            "source": "hls", "source_lang": "ja", "viewer_lang": "ko",
            "backend": "local-m2m100", "asr_backend": "tcpp-lite",
            "media_base": 0.0, "audio_s": 5.0, "lines": 0}
    store.save_session({**base, "id": "vid-1", "record_video": True}, "")
    assert live.resume("vid-1")["resumed"]
    assert live.get("vid-1").record_video is True
    live._sessions.clear()

    store.save_session({**base, "id": "vid-2", "record_video": False}, "")
    assert live.resume("vid-2")["resumed"]
    assert live.get("vid-2").record_video is False
    live._sessions.clear()

    store.save_session({**base, "id": "vid-3"}, "")            # stored before the flag
    assert live.resume("vid-3")["resumed"]
    assert live.get("vid-3").record_video is False
    live._sessions.clear()


# ---- Switching the saving on a session that is already running ------------------
# Both flags used to be settled in __init__ and never move again, which put the
# decision at the one moment a broadcast makes it hardest: before it has started.
# The two halves of the switch cost very different things, and that difference is
# what these pin.

def _registered(s: live.LiveSession) -> live.LiveSession:
    """Put a session in the registry, which is where `set_record` looks."""
    live._sessions[s.id] = s
    return s


class _Terminates(_FakeFF):
    """An ffmpeg that writes nothing down its pipe and writes down being asked to end."""

    def __init__(self):
        super().__init__(b"")
        self.terminated = 0

    def terminate(self):
        self.terminated += 1


def test_the_audio_switch_opens_and_closes_the_file_with_nothing_interrupted():
    """Saving the audio is switched on the bytes that are already flowing.

    `_record` is handed blocks the reading side has already read, so turning it
    on opens a WAV on the next one and turning it off closes the one that is
    open. No ffmpeg is involved and nothing is resolved again -- the process
    reading the broadcast must not be touched at all, because there is nothing
    about it that the answer changes.
    """
    s = _registered(_hls())
    s._ff = _Terminates()
    assert live.set_record(s.id, record=True) == {
        "record": True, "record_video": False, "reconnect": False}
    s._record(_pcm(1600))
    first = s._wav_path
    assert first and s.status()["recording"] == first

    assert live.set_record(s.id, record=False)["record"] is False
    s._record(_pcm(1600))                    # arrives after the switch: not in the file
    with wave.open(first, "rb") as w:
        assert w.getnframes() == 1600
    # The process reading the broadcast was never touched, in either direction.
    assert s._ff.terminated == 0 and s._respawn is False

    # And on again: a second file, not a continuation of the first. `recording_s`
    # is the length of the file `recording` names, so both start over.
    live.set_record(s.id, record=True)
    assert s.status()["recording"] == "" and s.status()["recording_s"] == 0
    s._record(_pcm(800))
    assert s._wav_path != first
    s._close_recording()
    with wave.open(s._wav_path, "rb") as w:
        assert w.getnframes() == 800


def test_the_video_switch_asks_for_the_reading_ffmpeg_to_be_stood_up_again():
    """Saving the video cannot be switched on a running process.

    The ffmpeg now reading was given a rendition and an output list chosen for
    the answer that held when it started -- audio-only with one output, or muxed
    with two -- and neither can be changed under it. So the switch ends it and
    lets the reading thread stand the next one up, which is the only code that
    knows how to find the place again. It is taken at once rather than left for
    the next natural break: a broadcast can run for hours without one.
    """
    s = _registered(_hls())
    s._ff = _Terminates()
    res = live.set_record(s.id, record_video=True)
    assert res == {"record": False, "record_video": True, "reconnect": True}
    assert s._respawn is True and s._ff.terminated == 1
    assert s._saving_video() is True

    # Off is a respawn too: the process now running is the muxed one with two
    # outputs, and it goes on writing the mp4 until it is replaced.
    s._respawn = False
    assert live.set_record(s.id, record_video=False)["reconnect"] is True
    assert s._respawn is True and s._ff.terminated == 2

    # Asking for what is already true changes nothing and breaks nothing.
    s._respawn = False
    assert live.set_record(s.id, record_video=False)["reconnect"] is False
    assert s._respawn is False and s._ff.terminated == 2


def test_a_requested_break_does_not_spend_a_reconnect_attempt(monkeypatch):
    """`HLS_RECONNECT_TRIES` is there to notice reception that will not come
    back, and a process we ended ourselves says nothing about that.

    The session here has already used every attempt it has. A break that
    happened to it gives up on the spot; the same break, asked for, starts the
    count over and reattaches.
    """
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    monkeypatch.setattr(live.subprocess, "Popen", lambda cmd, **kw: _FakeFF(b""))

    def one_reattach(s):
        """Run the read loop with every attempt already spent. A playlist comes
        back for the first reattach and the broadcast is over after it, so the
        loop ends either way -- what differs is whether it reattaches at all."""
        asked = []
        s._ff = _FakeFF(b"")

        def resolve(reconnect=False):
            asked.append(reconnect)
            return ("src", -2) if len(asked) == 1 else (None, None)
        s._resolve_hls = resolve
        s._stop.wait = lambda t: False
        s._read_until_end(live.CHUNK * 2, attempt=live.HLS_RECONNECT_TRIES)

    lost = _hls()
    one_reattach(lost)
    assert lost.stopped_by == "stream" and lost.state == "error"

    asked = _hls()
    asked._respawn = True
    one_reattach(asked)
    assert asked.stopped_by == "ended" and asked.state != "error"
    assert asked._respawn is False           # read once and cleared


def test_a_requested_break_does_not_count_against_the_video_part_budget(monkeypatch):
    """`VIDEO_RECONNECT_TRIES` is there to notice an mp4 output that keeps
    failing, and a part cut short because the user pressed the switch is not one.

    Left uncounted it would be: this session is one bad part away from giving up
    on the video, and the part now open was started a moment ago.
    """
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    monkeypatch.setattr(live.subprocess, "Popen", lambda cmd, **kw: _FakeFF(b""))
    s = _registered(_hls())
    s.record_video = True
    s._ff = _Terminates()
    s._vid_bad = live.VIDEO_RECONNECT_TRIES - 1
    s._vid_began = time.time()                       # a part opened a moment ago
    assert live.set_record(s.id, record_video=False)["reconnect"] is True
    s._spawn_ffmpeg("src", -2)                       # where a part is judged
    assert s._vid_bad == live.VIDEO_RECONNECT_TRIES - 1
    assert s._vid_error == "" and s.status()["recording_video_error"] == ""


def test_turning_the_video_on_again_is_a_genuine_retry():
    """Giving up on the video is permanent for the session, and that is what
    makes the switch worth having.

    One `resolve_video` that came back wrong at four in the morning, or five bad
    parts in a row, and an overnight broadcast saves nothing for the rest of its
    run however long that is. `_vid_error` is what makes it stick, so asking for
    the video again clears it -- asking is exactly the request that should mean
    "try once more".
    """
    s = _registered(_hls())
    s.record_video = True
    s._ff = _Terminates()
    s._video_failed(RuntimeError("yt-dlp found no rendition with the picture in it"))
    assert s._saving_video() is False

    assert live.set_record(s.id, record_video=False)["record_video"] is False
    res = live.set_record(s.id, record_video=True)
    assert res["record_video"] is True and res["reconnect"] is True
    assert s._vid_error == "" and s._vid_bad == 0 and s._saving_video() is True
    assert s.status()["recording_video_error"] == ""


def test_a_pushed_session_switches_its_audio_and_has_no_video_to_switch():
    """A microphone or tab session uploads sound and nothing else, so
    `record_video` is dropped there the same way `__init__` drops it. The audio
    half is the half that matters for that source and it has to keep working.
    """
    s = _registered(live.LiveSession("", None, "ko", "local-m2m100", source="mic",
                                     title="m"))
    s._tr = None
    assert live.set_record(s.id, record=True)["record"] is True
    assert s.feed(_pcm(live.CHUNK * 2))["ok"]
    assert s.status()["recording"] and s.status()["recording_s"] > 0

    res = live.set_record(s.id, record_video=True)
    assert res["record_video"] is False and res["reconnect"] is False
    assert s.record_video is False and s.status()["record_video"] is False
    s.stop()


def test_a_switch_is_answered_and_a_mistake_is_not_swallowed():
    """A session that is not there, and a request that asks for nothing."""
    assert live.set_record("no-such-session", record=True)["error"] == "no such session"
    s = _registered(_hls())
    assert "required" in live.set_record(s.id)["error"]
    assert s.record is False and s.record_video is False


def test_a_switch_survives_a_restart(monkeypatch):
    """The flags ride in `status()`, so `_persist` puts them in SQLite and
    `resume` reads them back -- a session switched at midnight and resumed at
    two has to come back switched. Nothing was written for this; what the test
    is for is that nothing has to be.
    """
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)
    s = _registered(_hls())
    s._ff = _Terminates()
    live.set_record(s.id, record=True, record_video=True)
    st = store.session(s.id)
    assert st["record"] is True and st["record_video"] is True

    store.save_session({**st, "state": "stopped", "stopped_by": "user"}, "")
    live._sessions.clear()
    assert live.resume(s.id)["resumed"]
    back = live.get(s.id)
    assert back.record is True and back.record_video is True


# ---- One broadcast, one WAV ------------------------------------------------
# The Python-side writer exists so that the file survives everything the ffmpeg
# under it does not: it is written across reconnects, and only a stop or the
# unticked box finishes it. What these pin is the one place that rule was broken
# -- not by a reconnect, which never touched the file, but by the stop racing
# ffmpeg's last block. Found against a live YouTube broadcast on 2026-09-15: two
# good recordings and, beside them, a WAV of 0 bytes.

def _wavs() -> list[str]:
    d = live.paths.recordings_dir()
    return sorted(f for f in (os.listdir(d) if os.path.isdir(d) else [])
                  if f.endswith(".wav"))


@pytest.mark.parametrize("requested", [False, True])
def test_a_reconnect_of_either_kind_leaves_the_open_wav_alone(requested):
    """One broadcast, one file, however many ffmpegs it took to receive it.

    A break that happened to the session and one it asked for itself -- the
    video switch, which cannot be made under a running process -- go down the
    same path, and neither may touch the WAV. That is the whole reason the
    recorder is written in Python instead of as a second ffmpeg output. If it
    were broken here, every long broadcast that reconnected would have been
    splitting its recording into a file per ffmpeg.
    """
    s = _registered(_hls(record=True))
    first, second = _pcm(live.CHUNK), _pcm(live.CHUNK)
    s._ff = _FakeFF(first)
    if requested:
        s.record_video = True
        assert live.set_record(s.id, record_video=False)["reconnect"] is True
        assert s._respawn is True
    s._spawn_ffmpeg = lambda src, start_index: setattr(s, "_ff", _FakeFF(second))
    resolves = []

    def resolve(reconnect=False):
        resolves.append(reconnect)
        return ("src", -2) if len(resolves) == 1 else (None, None)
    s._resolve_hls = resolve
    s._stop.wait = lambda t: False
    s._read_loop()

    assert resolves == [True, True]        # one reattach, then the broadcast ended
    assert _wavs() == [os.path.basename(s._wav_path)]
    with wave.open(s._wav_path, "rb") as w:
        assert w.readframes(w.getnframes()) == first + second
    live._sessions.clear()


class _HoldsTheLastBlock(_FakeStdout):
    """ffmpeg's pipe with its last block still in it when the stop lands.

    A real one does this by itself: `stop` terminates ffmpeg, and ffmpeg hands
    over what it was holding before it goes -- later still when it is also
    writing an mp4, because it flushes the last fragment first. Here the read
    waits until the stop has been all the way through.
    """

    def __init__(self, data: bytes):
        super().__init__(data)
        self.at_the_tail = threading.Event()
        self.stopped = threading.Event()

    def read(self, n: int) -> bytes:
        if 0 < self.pos < len(self.data):
            self.at_the_tail.set()
            self.stopped.wait(5.0)
        return super().read(n)


def test_the_block_ffmpeg_was_holding_at_the_stop_opens_no_second_wav():
    """The stray empty recording, and the reason there was one.

    `stop` used to close the WAV from the API thread. ffmpeg's last block --
    under one VAD chunk, and the one the transcriber cannot take -- arrived
    after that, found no open file and `record` still ticked, and opened one of
    its own: a new timestamp, and nothing left alive to close it. `wave` writes
    its header into a buffered file, so what was left on disk was 0 bytes, in
    the one directory mimiwatch never deletes from. The recording itself lost
    nothing, which is why it went unnoticed; an empty file in there reads as a
    recording that failed.

    The closing belongs to the thread that does the writing, so the block lands
    in the file it was always meant to land in and the count of files is one.
    """
    s = _registered(_hls(record=True))
    data = _pcm(live.CHUNK + 160)          # one whole block, then a short one
    ff = _FakeFF(b"")
    ff.stdout = _HoldsTheLastBlock(data)
    s._ff = ff
    s._resolve_hls = lambda reconnect=False: (None, None)
    s._stop.wait = lambda t: False
    s._rx = threading.Thread(target=s._read_loop, name=f"rx-{s.id}", daemon=True)
    s._rx.start()
    assert ff.stdout.at_the_tail.wait(5.0)
    s.stop()                               # the user's stop, mid-block
    ff.stdout.stopped.set()
    s._rx.join(5.0)
    assert not s._rx.is_alive()

    assert _wavs() == [os.path.basename(s._wav_path)]
    with wave.open(s._wav_path, "rb") as w:
        # And the tail is in it: it is audio that exists nowhere else, which is
        # what `_read_until_end` records it ahead of the transcriber for.
        assert w.readframes(w.getnframes()) == data
    assert s.status()["recording_error"] == ""
    live._sessions.clear()


def test_unticking_the_audio_box_is_what_closes_the_file():
    """The other half of the rule: a reconnect never closes the WAV, and the box
    always does -- while the session goes on receiving, with no break anywhere.

    The file has to be complete the moment the box comes off, because what
    follows it is a session that is still running and still writing nothing.
    """
    s = _registered(_hls(record=True))
    kept, dropped = _pcm(live.CHUNK), _pcm(live.CHUNK)
    s._ff = _FakeFF(kept)
    s._resolve_hls = lambda reconnect=False: (None, None)
    s._stop.wait = lambda t: False
    s._read_until_end(live.CHUNK * 2, attempt=0)      # reads `kept`, then ends
    path = s._wav_path
    assert live.set_record(s.id, record=False)["record"] is False
    with wave.open(path, "rb") as w:                  # closed, and complete
        assert w.readframes(w.getnframes()) == kept
    s._record(dropped)                                # after the switch: nowhere
    assert _wavs() == [os.path.basename(path)]
    assert s._wav is None
    live._sessions.clear()


def test_a_recorder_that_never_got_a_sample_leaves_nothing_behind(monkeypatch):
    """A WAV that was opened and never written to is taken away, not left.

    The only way to reach it is a write that failed on the very first block --
    a full disk, a volume that went read-only -- and `wave` writes a 44-byte
    header on close whatever happened. A 44-byte file in the recordings
    directory is a recording that failed wearing the clothes of one that
    worked, and nothing in there is ever deleted automatically, so it would sit
    there. The status stops naming it too.
    """
    real_open = live.wave.open

    def full_disk(path, mode):
        w = real_open(path, mode)
        w.writeframes = lambda b: (_ for _ in ()).throw(OSError("no space left"))
        return w
    monkeypatch.setattr(live.wave, "open", full_disk)
    s = live.LiveSession("", None, "ko", "local-m2m100", source="mic", title="m",
                         record=True)
    s._tr = None
    assert s.feed(_pcm(live.CHUNK))["ok"]             # the subtitles carry on
    assert "no space left" in s.status()["recording_error"]
    assert s.status()["recording"] == "" and s.status()["recording_s"] == 0
    assert _wavs() == []
    s.stop()
    assert _wavs() == []
    live._sessions.clear()
