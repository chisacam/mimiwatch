"""라이브 세션의 발행 경로와 재접속. 모델은 올리지 않습니다."""
import json
import numpy as np
import pytest
import subprocess
import threading
import time

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
    # 예전에는 최근 줄(1)의 번호를 재사용해 그 줄을 덮어썼습니다.
    assert [(c["id"], c["text"]) for c in rows] == [(1, "全然違う文章です"), (2, "まったく別の内容")]
    assert s.lines == 2


def test_short_final_needs_exact_containment():
    assert live._covers("はい", "はい、そうです") is True
    assert live._covers("はい", "いいえ") is False
    assert live._covers("무기도 풀제열이야", "무기도 풀제일이야?") is True   # 글자가 조금 달라도 겹침으로


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
    assert s.replay_since("99") is None          # 기록 밖: 백로그 전부
    assert s.replay_since("abc") is None


def test_stop_marks_user_and_status_carries_it(session):
    s = session
    s.stop()
    assert s.stopped_by == "user" and s.status()["stopped_by"] == "user"


def _scenario(session, fake_ffmpeg, outcomes, tries=3):
    """읽기 스레드(`_read_loop`)를 그 자리에서 끝까지 돌린 뒤, 받아 적는 쪽(`_consume`)이
    링에서 무엇을 꺼내는지 봅니다. 둘은 실제로는 다른 스레드지만 링이 순서를 지키므로
    차례로 돌려도 같은 결과입니다."""
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
    assert seq == "SSSNSSN"                      # 3조각, 비움, 다시 2조각, 비움
    assert session.stopped_by == "ended" and session.state == "stopped"
    assert resolves[0] == 100.3                  # 끊긴 자리 = base + 받은 0.3초
    assert abs(resolves[1] - 200.2) < 1e-6       # 새 base 에서 0.2초 뒤에 끝남
    notes = [c for c in store.cues(session.id) if c["kind"] == "note"]
    assert len(notes) == 1 and "7 s" in notes[0]["text"]
    # 안내는 링을 거쳐 새 시간 기준이 적용된 **뒤에** 발행됩니다. 옛 조각들 사이에
    # 끼어 200초대 시각을 달면 스크립트에서 자리가 뒤바뀝니다.
    assert notes[0]["t"] == 200.0
    # 받아 적는 시계도 새 기준에서 마지막 조각까지 왔습니다.
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

    def read_then_stop(n):          # 첫 조각을 읽은 직후 사용자가 「중단」
        raw = read(n)
        s.stop()
        return raw
    s._ff.stdout.read = read_then_stop
    s._resolve_hls = lambda reconnect=False: pytest.fail("중단 뒤에 다시 붙으려 했습니다")
    s._read_loop()
    # 받아 적는 쪽은 멈춤 깃발을 보고 남은 조각을 건드리지 않습니다.
    assert list(s._consume()) == []
    assert s.stopped_by == "user" and s.state == "stopped"


def test_ring_drops_oldest_audio_but_keeps_markers():
    r = live.Ring(1.0)                           # 10조각
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
    assert kinds.count("audio") == 10 and kinds.index("flush") == 2   # 0.6·0.7 뒤에 표식
    assert abs(items[0][1] - 0.6) < 1e-9         # 가장 오래된 것부터 버렸습니다


def test_consumer_clock_follows_the_reader_after_a_gap(session):
    """링이 넘쳐 조각을 버린 뒤에도 자막 시각은 그 조각이 방송에서 있던 자리입니다."""
    s = session
    s._ring = live.Ring(1.0)
    s.media_base = 100.0
    frame = np.zeros(live.CHUNK, dtype=np.float32)
    for i in range(1, 31):                       # 3초를 받았지만 1초만 남습니다
        s._recv_s = i * 0.1
        s._ring.push(("audio", s._recv_s, frame))
    s._ended = True
    s._ring.push(("end",))
    got = [c for c in s._consume() if isinstance(c, np.ndarray)]
    assert len(got) == 10
    assert abs(s.audio_s - 3.0) < 1e-9           # 더한 값(1.0)이 아니라 읽기 시계의 값
    s.publish_line("final", "こんにちは", "ja", "")
    assert store.cues(s.id)[-1]["t"] == 103.0


def test_delete_refuses_running_session(session):
    live._sessions[session.id] = session
    assert "error" in live.delete(session.id)
    live._sessions.clear()
    store.save_session(session.status())
    assert live.delete(session.id) == {"deleted": session.id}
    assert live.delete(session.id) == {"error": "no such session"}


# ---- 번역 순서와 정제본에 덮인 줄 ------------------------------------------------

class _SlowTranslator:
    """부르는 순서와, 그 사이에 정제본이 들어올 틈을 만들어 주는 번역기."""

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
    """정제본이 흡수한 확정 줄의 번역이 늦게 도착해 정제본의 번역을 덮던 경합.

    번역기가 첫 줄을 옮기는 사이에 정제본이 그 줄을 흡수합니다. 예전에는 그
    옛 번역이 같은 id(정제본이 물려받은 것)로 발행·저장되어, 화면과 DB에
    정제본 아래 부분 번역이 남았습니다.
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
    # 정제본은 첫 줄을 번역하는 도중에 줄에 서므로, 끝 표시를 넣기 전에 그것이
    # 처리될 때까지 기다립니다(세션이 끝날 때는 정제기가 먼저 닫히므로 실제로는
    # 이 순서가 저절로 지켜집니다).
    import time
    deadline = time.time() + 5
    while time.time() < deadline and len(s._tr.calls) < 2:
        time.sleep(0.01)
    s._close_translator()
    got = _translations(s)
    # 확정 둘의 번역은 버려지고, 정제본(id 1)의 번역만 나갑니다.
    assert got == [(1, "T:こんにちは 元気ですか")]
    rows = store.cues(s.id)
    assert [(c["id"], c["translations"]) for c in rows] == \
        [(1, {"local-m2m100": "T:こんにちは 元気ですか"})]
    # 흡수된 둘째 줄은 번역기를 부르지도 않았습니다 -- Gemma 시간을 아낍니다.
    assert s._tr.calls == ["こんにちは", "こんにちは 元気ですか"]


def test_close_translator_drains_pending_lines(session):
    s = session
    s._tr = _SlowTranslator(delay=0.01)
    for n in range(5):
        s.audio_s = n * 1.0
        s.publish_line("final", f"tail {n}", "ja", "")
    s._release()                                  # 세션이 끝날 때 부르는 것
    assert len(_translations(s)) == 5
    assert s._tr is None


def test_resume_uses_the_engines_the_caller_picked(monkeypatch):
    """멈춘 사이 「관리」에서 바꾼 엔진으로 이어받습니다. 모르는 id 면 저장된 것을 씁니다."""
    st = {"id": "resume-1", "state": "stopped", "stopped_by": "user", "url": "https://x/live",
          "source": "hls", "source_lang": "ja", "viewer_lang": "ko", "backend": "local-m2m100",
          "asr_backend": "tcpp-lite", "media_base": 10.0, "audio_s": 5.0, "lines": 0}
    store.save_session(st, "")
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)   # 실제로 받지는 않습니다
    got = live.resume("resume-1", asr_backend_id="tcpp-best", backend_id="local-gemma")
    assert got["resumed"]
    s = live.get("resume-1")
    assert s.asr_backend_id == "tcpp-best" and s.backend_id == "local-gemma"
    live._sessions.clear()
    store.save_session(st, "")                 # _run 이 가짜라 상태가 starting 으로 남았습니다
    got = live.resume("resume-1", asr_backend_id="no-such", backend_id="")
    s = live.get("resume-1")
    assert s.asr_backend_id == "tcpp-lite" and s.backend_id == "local-m2m100"
    live._sessions.clear()


def test_resume_can_switch_the_audio_source(monkeypatch):
    """주소로 받던 세션을 탭 소리로, 탭 소리 세션을 주소로 이어받습니다. 자막은 같은 세션입니다."""
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
    assert "error" in live.resume("tab-1", source="hls")            # 주소가 없습니다
    got = live.resume("tab-1", source="hls", url="https://y/live")
    s = live.get("tab-1")
    assert got["source"] == "hls" and s.url == "https://y/live"
    live._sessions.clear()


def test_tab_feed_slices_into_frames_and_idle_flushes_once():
    """탭 세션의 읽기는 feed() 입니다. 2초 덩어리가 0.1초 조각 스무 개가 되어 링에 들고,
    소리가 끊기면 걸린 발화를 한 번만 확정(None)하고, 「중단」이면 물러납니다."""
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
        assert next(gen) is None                 # 무음 2초(0.5초 × 4): 한 번 비웁니다
    finally:
        live.Ring.pop = real_pop
    s.stop()
    assert list(gen) == []
    assert s.state == "stopped" and s.feed(b"\x00" * 3200)["error"]


def test_tab_ring_drops_oldest_when_the_browser_outruns_asr():
    s = live.LiveSession("", None, "ko", "local-m2m100", source="tab", title="t")
    s._ring.set_max(1.0)                         # 시험용으로 1초만
    r = s.feed(b"\x00" * (live.CHUNK * 2 * 15))
    assert r["queued_s"] == 1.0 and abs(r["dropped_s"] - 0.5) < 1e-9


# ---- 초점: 받아 적는 세션은 한 번에 하나 ------------------------------------------

def test_unfocus_flushes_and_ends_the_generator(session):
    s = session
    frame = np.zeros(live.CHUNK, dtype=np.float32)
    for i in range(1, 4):
        s._ring.push(("audio", i * 0.1, frame))
    gen = s._consume()
    assert isinstance(next(gen), np.ndarray)
    s.set_focus(False)
    rest = list(gen)
    assert rest[-1] is None                      # 걸린 발화를 확정하고 물러납니다
    assert s.state == "starting" and not s._ended and s._ring.seconds() > 0   # 세션은 그대로
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
    b.set_focus(False)                           # b 는 소리만 받는 대기 세션
    frame = np.zeros(live.CHUNK, dtype=np.float32)
    for i in range(1, 21):
        b._ring.push(("audio", i * 0.1, frame))
    ta = threading.Thread(target=a._transcribe, args=("src", -2), daemon=True)
    tb = threading.Thread(target=b._transcribe, args=("src", -2), daemon=True)
    ta.start(); tb.start()
    deadline = time.time() + 5
    while (a.id, "start") not in log and time.time() < deadline:
        time.sleep(0.05)
    assert (a.id, "start") in log and not any(e[0] == b.id for e in log)   # b 는 기다립니다
    # 초점을 b 로. 옛 것을 먼저 끄고 새 것을 켭니다(multiview_focus 의 순서).
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
    assert ended_b and ended_b[0][2] == 20      # 대기 중 링에 고인 스무 조각을 먼저 받아 적었습니다
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
    store.save_session({**base, "id": "old-1"}, "")   # 옛 기록에는 recv_t 가 없습니다
    live.resume("old-1")
    assert live.get("old-1").resume_from == 15.0
    live._sessions.clear()


# ---- 멀티뷰 묶음 ------------------------------------------------------------------

def _drain(q):
    out = []
    while True:
        try:
            out.append(__import__("json").loads(q.get_nowait()))
        except Exception:
            return out


def test_multiview_groups_sessions_and_moves_focus(monkeypatch):
    import bus
    monkeypatch.setattr(live.LiveSession, "_run", lambda self: None)   # 실제로 받지는 않습니다
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
        assert a._focus.is_set() and not b._focus.is_set()      # 보던 것이 초점, 새것은 대기
        assert outsider._stop.is_set() and outsider.stopped_by == "user"   # 묶음 밖은 멈춤
        assert not a._stop.is_set() and not b._stop.is_set()
        assert any(e["type"] == "multiview" and e["focus"] == a.id for e in _drain(q))

        got = live.multiview_focus(gid, b.id)
        assert got["previous"] == a.id and got["focus"] == b.id
        assert b._focus.is_set() and not a._focus.is_set()
        assert live.multiview_focus(gid, "nope")["error"]
        assert live.multiview_focus("nope", b.id)["error"]

        c = live.get(live.multiview_add(gid, {"url": "https://x/c"}, "ja", "ko", "local-m2m100")["id"])
        assert c.group == gid and not c._focus.is_set() and len(live.multiview_status(gid)["members"]) == 3

        # 초점 타일을 닫으면 그 세션은 멈추고 초점은 남은 첫 멤버로
        got = live.multiview_remove(gid, b.id)
        assert b._stop.is_set() and got["focus"] == a.id and a._focus.is_set() and b.group == ""

        # 세션이 스스로 끝나도(_retire) 묶음이 따라옵니다. 마지막 멤버가 끝나면 묶음도 없어집니다.
        live._retire(a)
        assert live.multiview_status(gid)["focus"] == c.id and c._focus.is_set() and a.group == ""
        live._retire(c)
        assert live.multiview_status(gid) is None
        assert any(e["type"] == "multiview" and e.get("deleted") for e in _drain(q))

        # 잘못된 묶음 요청은 아무것도 남기지 않습니다
        assert live.multiview_start([], "ja", "ko", "local-m2m100")["error"]
        assert live.multiview_start([{"url": f"https://x/{i}"} for i in range(5)], "ja", "ko", "local-m2m100")["error"]
        before = set(live._sessions)
        assert live.multiview_start([{"url": "https://x/d"}, {"session": "nope"}], "ja", "ko", "local-m2m100")["error"]
        leftovers = [live.get(i) for i in set(live._sessions) - before]
        assert all(s._stop.is_set() and s.group == "" for s in leftovers)
        assert not live._groups

        # 혼자 받기(start) 는 예전처럼 전부 멈춥니다 -- 묶음 멤버도요
        g2 = live.multiview_start([{"url": "https://x/e"}, {"url": "https://x/f"}], "ja", "ko", "local-m2m100")
        e, f = (live.get(m["id"]) for m in g2["members"])
        assert e._focus.is_set() and not f._focus.is_set()        # focus 를 안 주면 첫 멤버
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
    assert live.site_of(yt) == {"site": "youtube", "video_id": "abc123XYZ_-", "channel": "UCx"}
    tw = {"extractor_key": "TwitchStream", "webpage_url_domain": "twitch.tv", "id": "40500071752",
          "uploader_id": "Monstercat", "display_id": "monstercat"}
    assert live.site_of(tw) == {"site": "twitch", "video_id": "40500071752", "channel": "monstercat"}
    # 정보가 비어도 주소에서 로그인명을 건집니다
    assert live.site_of({}, "https://www.twitch.tv/Shroud?x=1")["channel"] == "shroud"
    gen = {"extractor_key": "Generic", "webpage_url_domain": "cdn.example", "id": "master"}
    assert live.site_of(gen, "https://cdn.example/live/master.m3u8") == {
        "site": "other", "video_id": "master", "channel": ""}
    assert live.looks_like_m3u8("https://cdn.example/a/b.m3u8?tok=1")
    assert not live.looks_like_m3u8("https://www.youtube.com/watch?v=x")


def test_manifest_info_sees_the_end_of_a_recording():
    vod = "data:application/vnd.apple.mpegurl,%23EXTM3U%0A%23EXT-X-TARGETDURATION:10%0A%23EXTINF:9.0,%0Aa.ts%0A%23EXTINF:9.0,%0Ab.ts%0A%23EXT-X-ENDLIST%0A"
    live_ = "data:application/vnd.apple.mpegurl,%23EXTM3U%0A%23EXT-X-TARGETDURATION:2%0A%23EXTINF:2.0,%0Aa.ts%0A"
    assert live.manifest_info(vod)["ended"] is True and live.manifest_info(vod)["window_s"] == 18.0
    assert live.manifest_info(live_)["ended"] is False


def _fake_playlist(monkeypatch, session, pdt="2026-01-01T00:20:00Z", release_ts=None, window_s=600.0):
    """yt-dlp -j 와 재생목록 읽기를 흉내 냅니다. 첫 조각은 방송 시작 1200초 뒤, 창은 600초."""
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
    s.resume_from = 1000.0                    # 1000초에 멈췼고, 지금 라이브 끝은 1200+600=1800초
    src, idx = s._resolve_hls()
    assert idx == -2 and s._recv_base == 1800.0 and s.gap_s == 800.0   # 되감지 않고 빠진 800초만 적습니다


def test_reconnect_inside_the_session_still_rewinds(session, monkeypatch):
    s = session
    _fake_playlist(monkeypatch, s)
    s.resume_from = 1500.0                    # 창(1200~1800) 안에서 끊겼습니다
    s._rewind = True                          # _reconnect 가 세우는 깃발
    src, idx = s._resolve_hls(reconnect=True)
    assert idx == 150 and s.gap_s == 0.0      # 300초 뒤 = 2초짜리 150번째 조각부터, 잃는 것 없음


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
        # 없는 세션은 실패하고 묶음도 남지 않습니다
        assert live.multiview_start([{"session": "nope"}], "ja", "ko", "local-m2m100")["error"]
        assert list(live._groups) == [g["id"]]
    finally:
        live._sessions.clear()
        live._groups.clear()


def test_ffmpeg_is_spawned_without_inheriting_std_handles(session, monkeypatch):
    """0.3.1 의 윈도우 사용자가 `_spawn_ffmpeg` 에서 WinError 6 으로 죽었습니다.

    stdin·stderr 를 주지 않으면 `Popen` 이 부모 것을 물려주려고 복제하는데,
    표준 핸들이 성치 않은 채로 뜬 프로세스에서는 그 복제가 실패합니다.
    """
    seen = {}
    monkeypatch.setattr(live.stream, "ffmpeg_cmd", lambda: "ffmpeg")
    monkeypatch.setattr(live.subprocess, "Popen",
                        lambda cmd, **kw: seen.update(cmd=cmd, kw=kw) or object())
    session._spawn_ffmpeg("https://example.invalid/live.m3u8", -2)
    assert "-nostdin" in seen["cmd"]
    assert seen["kw"]["stdout"] is subprocess.PIPE
    assert seen["kw"]["stdin"] == subprocess.DEVNULL   # 물려받은 핸들에 기대지 않습니다
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
