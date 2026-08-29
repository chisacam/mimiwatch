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
