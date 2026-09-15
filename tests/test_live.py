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
# A second ffmpeg, given a muxed URL of its own and copying it to disk. None of it
# is spawned here: `video_plan` exists as a seam so the argv can be read without
# running it, the same way `burn.plan` can be.

def test_video_plan_copies_the_picture_and_re_encodes_only_the_sound():
    """The picture must never go through an encoder.

    Re-encoding it would put the whole of a GPU-less machine's CPU behind
    something it is already paying for once in transcription; what this feature
    costs is disk. The audio is the exception and has to be, because HLS hands
    over ADTS AAC that mp4 will not take -- `video_plan`'s docstring has why the
    bitstream filter is not the answer.
    """
    cmd = live.video_plan("https://example.invalid/muxed.m3u8", "/tmp/out.mp4")
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-vn" not in cmd and "libx264" not in cmd
    assert cmd[-1] == "/tmp/out.mp4"
    assert cmd[cmd.index("-i") + 1] == "https://example.invalid/muxed.m3u8"


def test_video_plan_writes_a_fragmented_mp4():
    """A killed process must still leave a file that plays.

    A plain mp4 writes its index when the process ends, so `kill -9` -- how a
    program people close ends most of the time -- would leave nothing a player
    opens. Fragmented, the index is written along the way and a file cut off in
    the middle plays up to its last complete fragment.
    """
    cmd = live.video_plan("https://example.invalid/muxed.m3u8", "/tmp/out.mp4")
    flags = cmd[cmd.index("-movflags") + 1]
    for f in ("frag_keyframe", "empty_moov", "default_base_moof"):
        assert f in flags


def test_the_next_video_part_is_numbered_beside_the_first():
    """The signed URL expires, so one broadcast can leave several files. They are
    numbered rather than stamped afresh, so they sort together and read in order
    -- `burn.out_path_for` numbers a re-burn the same way."""
    d = live.paths.recordings_dir()
    os.makedirs(d, exist_ok=True)
    first = live.video_part_path("20260915-120000-abc")
    assert first == os.path.join(d, "20260915-120000-abc.mp4")
    open(first, "wb").close()
    second = live.video_part_path("20260915-120000-abc")
    assert second == os.path.join(d, "20260915-120000-abc (2).mp4")
    open(second, "wb").close()
    assert live.video_part_path("20260915-120000-abc") == \
        os.path.join(d, "20260915-120000-abc (3).mp4")


def test_a_session_not_asked_for_video_starts_no_recorder(monkeypatch):
    """Off is the default and off spawns nothing -- no ffmpeg, and not even the
    yt-dlp call that would resolve a URL for it."""
    monkeypatch.setattr(live, "resolve_video",
                        lambda url: pytest.fail("resolve_video was called"))
    for s in (_hls(), live.LiveSession("https://example.invalid/live", "ja", "ko",
                                       "local-m2m100", record_video=False)):
        s._tr = None
        assert s.record_video is False
        s._start_video()
        assert s._vid_thread is None
        assert s.status()["recording_video"] == ""
        assert s.status()["recording_video_bytes"] == 0


def test_a_pushed_source_has_no_video_to_save():
    """The extension and the capture page upload sound and nothing else, so the
    flag is dropped rather than carried as a promise the session cannot keep. The
    page hides the box for those sources; this is the other half of it."""
    for src in ("tab", "mic"):
        s = live.LiveSession("", None, "ko", "local-m2m100", source=src, title="t",
                             record_video=True)
        assert s.record_video is False
        assert s.status()["record_video"] is False
    assert live.LiveSession("https://example.invalid/live", "ja", "ko",
                            "local-m2m100", record_video=True).record_video is True


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
