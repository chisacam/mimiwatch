"""라이브 세션의 발행 경로와 재접속. 모델은 올리지 않습니다."""
import numpy as np

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
    s.publish_line("note", "⋯ 서버가 멈춘 사이 약 10초를 받지 못했습니다 ⋯", "ja", "")
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
    s = session
    live.HLS_RECONNECT_TRIES = tries
    s._ff = fake_ffmpeg(3)
    s.media_base = 100.0
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
        s.media_base = 200.0
        return "src", -2
    s._resolve_hls = fake_resolve
    s._spawn_ffmpeg = lambda src, idx: setattr(s, "_ff", fake_ffmpeg(2))
    s._stop.wait = lambda t: False
    seq = "".join("S" if isinstance(c, np.ndarray) else "N" for c in s._chunks())
    return seq, resolves


def test_hls_reconnects_inside_the_session(session, fake_ffmpeg):
    seq, resolves = _scenario(session, fake_ffmpeg, ["ok", "ended"])
    assert seq == "SSSNSSN"                      # 3조각, 비움, 다시 2조각, 비움
    assert session.stopped_by == "ended" and session.state == "stopped"
    assert resolves[0] == 100.3                  # 끊긴 자리 = base + 받은 0.3초
    assert abs(resolves[1] - 200.2) < 1e-6       # 새 base 에서 0.2초 뒤에 끝남
    notes = [c for c in store.cues(session.id) if c["kind"] == "note"]
    assert len(notes) == 1 and "7초" in notes[0]["text"]


def test_hls_gives_up_after_retries(session, fake_ffmpeg):
    seq, _ = _scenario(session, fake_ffmpeg, ["fail", "fail", "fail"], tries=3)
    assert seq == "SSSN"
    assert session.stopped_by == "stream" and session.state == "error"
    assert "이어받기" in session.error


def test_user_stop_does_not_reconnect(session, fake_ffmpeg):
    s = session
    s._ff = fake_ffmpeg(2)
    gen = s._chunks()
    next(gen)
    s.stop()
    assert list(gen) == []
    assert s.stopped_by == "user" and s.state == "stopped"


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
