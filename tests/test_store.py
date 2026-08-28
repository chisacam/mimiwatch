"""저장소: 자막 표의 규칙."""
import store


def test_replace_cues_keeps_edited_flags():
    # 통째로 갈아 끼우는 경로가 `edited`를 떨어뜨리던 것이 손편집 소실의 원인이었습니다.
    store.replace_cues("v", [
        {"start": 0, "end": 1, "text": "a", "edited": "tr", "translations": {"g": "사람"}},
        {"start": 1, "end": 2, "text": "b"},
    ])
    rows = store.cues("v")
    assert rows[0]["edited"] == "tr" and rows[0]["translations"] == {"g": "사람"}
    assert rows[1]["edited"] == ""
    assert [r["id"] for r in rows] == [1, 2]


def test_save_cue_refine_wipes_translation():
    store.save_cue("s", {"id": 1, "kind": "final", "t": 1.0, "text": "a", "lang": "ja"})
    store.save_translation("s", 1, "g", "T")
    assert store.cues("s")[0]["translations"] == {"g": "T"}
    store.save_cue("s", {"id": 1, "kind": "refine", "t": 1.0, "text": "ab", "lang": "ja"})
    # 정제본은 문장이 바뀐 것이니 옛 번역은 틀린 문장의 것입니다.
    assert store.cues("s")[0]["translations"] == {}


def test_delete_session_removes_cues_too():
    store.save_session({"id": "s1", "state": "stopped"})
    store.save_cue("s1", {"id": 1, "kind": "final", "t": 0, "text": "x", "lang": "ja"})
    assert store.delete_session("s1") is True
    assert store.session("s1") is None and store.cues("s1") == []
    assert store.delete_session("s1") is False


def test_sessions_limit_and_order():
    for i in range(5):
        store.save_session({"id": f"s{i}", "state": "stopped"})
    got = store.sessions(limit=3)
    assert len(got) == 3
    assert got[0]["id"] == "s4"          # 최근 것부터


def test_save_translation_clears_stale_mark_but_keeps_hand_mark():
    store.replace_cues("v", [{"start": 0, "end": 1, "text": "a", "translations": {"g": "old"}}])
    store.edit_cue("v", 1, text="a2")                 # 원문을 고쳤다 -> "text" 표시
    assert "text" in store.cues("v")[0]["edited"]
    store.save_translation("v", 1, "g", "new")        # 기계가 다시 번역
    assert "text" not in store.cues("v")[0]["edited"]
    store.edit_cue("v", 1, tr="hand", backend="g")
    store.save_translation("v", 1, "g", "machine")
    assert "tr" in store.cues("v")[0]["edited"]
