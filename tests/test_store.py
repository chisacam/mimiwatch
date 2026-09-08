"""The store: the rules of the cue table."""
import store


def test_replace_cues_keeps_edited_flags():
    # The wholesale replacement path dropping `edited` is what lost hand edits.
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
    # A refined line is a changed sentence, so the old translation belongs to the wrong one.
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
    assert got[0]["id"] == "s4"          # Most recent first


def test_save_translation_clears_stale_mark_but_keeps_hand_mark():
    store.replace_cues("v", [{"start": 0, "end": 1, "text": "a", "translations": {"g": "old"}}])
    store.edit_cue("v", 1, text="a2")                 # The source was edited -> the "text" mark
    assert "text" in store.cues("v")[0]["edited"]
    store.save_translation("v", 1, "g", "new")        # The machine translates it again
    assert "text" not in store.cues("v")[0]["edited"]
    store.edit_cue("v", 1, tr="hand", backend="g")
    store.save_translation("v", 1, "g", "machine")
    assert "tr" in store.cues("v")[0]["edited"]


def test_insert_cue_gets_next_id_but_reads_in_time_order():
    # A line a person typed in: the number comes after the last one (identity), the reading order is by time.
    store.replace_cues("v", [
        {"start": 0, "end": 2, "text": "a"},
        {"start": 10, "end": 12, "text": "c"},
    ])
    got = store.insert_cue("v", 5.0, "b", lang="ja")
    assert got["id"] == 3 and got["kind"] == "final" and got["lang"] == "ja"
    rows = store.cues("v")
    assert [r["text"] for r in rows] == ["a", "b", "c"]
    assert [r["id"] for r in rows] == [1, 3, 2]


def test_insert_cue_translation_is_hand_edited():
    store.replace_cues("v", [{"start": 0, "end": 1, "text": "a"}])
    # Written together with a translation it counts as hand-edited -- a bulk re-translation will not overwrite it.
    got = store.insert_cue("v", 2.0, "b", tr="비", backend="g")
    assert got["translations"] == {"g": "비"} and "tr" in got["edited"]
    # The source alone carries no mark -- a machine translation has to be able to land on it.
    got2 = store.insert_cue("v", 3.0, "c")
    assert got2["translations"] == {} and got2["edited"] == ""


def test_search_finds_source_and_translation():
    store.replace_cues("v1", [{"start": 0, "text": "hello mimi",
                               "translations": {"g": "미미 안녕"}}])
    store.replace_cues("v2", [{"start": 5, "text": "goodbye"}])
    assert [r["owner"] for r in store.search("mimi")] == ["v1"]
    assert [r["owner"] for r in store.search("미미")] == ["v1"]
    assert store.search("zzz") == []
    assert store.search("") == []


def test_search_keeps_step_with_cue_writes():
    store.save_cue("s", {"id": 1, "kind": "final", "t": 0, "text": "quantum fox"})
    assert [r["owner"] for r in store.search("quantum")] == ["s"]
    assert store.search("fox")
    store.edit_cue("s", 1, text="quantum cat")
    assert store.search("quantum")
    assert store.search("fox") == []
    store.delete_cue("s", 1)
    assert store.search("quantum") == []


def test_search_quotes_cannot_break_the_match():
    store.save_cue("s", {"id": 1, "kind": "final", "t": 0, "text": 'say "hi" loudly'})
    assert [r["owner"] for r in store.search('"hi"')] == ["s"]
    # An FTS operator coming in from the user is a plain word, not syntax.
    assert store.search("AND") == []
    assert store.search("NEAR") == []


def test_search_snippet_marks_the_word():
    store.save_cue("s", {"id": 1, "kind": "final", "t": 0, "text": "the quick brown fox"})
    r = store.search("fox")[0]
    assert "«" in r["snip"]


def test_owner_kind_and_title():
    store.save_session({"id": "s1", "state": "stopped", "title": "Stream A",
                        "url": "https://x"})
    assert store.owner_kind("s1") == "live"
    assert store.owner_title("s1") == "Stream A"
    store.save_doc("v9", {"title": "Video B"})
    assert store.owner_kind("v9") == "video"
    assert store.owner_title("v9") == "Video B"
    assert store.owner_title("nobody") == "nobody"


def test_fts_resync_repairs_a_drifted_table():
    store.save_cue("s", {"id": 1, "kind": "final", "t": 0, "text": "needle"})
    assert store.search("needle")
    with store._lock:
        db = store._connect()
        db.execute("DELETE FROM cues_fts")
        db.commit()
    assert store.search("needle") == []      # drifted
    store.init()                             # the start-up resync repairs it
    assert store.search("needle")
