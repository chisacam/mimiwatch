"""The store: the rules of the cue table."""
import json

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


def _fts_matches_a_rebuild():
    """The whole FTS table, row by row, equals what the start-up rebuild would
    write from `cues` as it stands -- recomputed here, not read back from the
    rebuild, and over every owner, so an orphan a delete left behind counts."""
    with store._lock:
        db = store._connect()
        got = sorted(tuple(r) for r in db.execute(
            "SELECT rowid, owner, cue_id, text, tr_text FROM cues_fts"))
        want = sorted((r["rowid"], r["owner"], r["cue_id"], r["text"],
                       " ".join(v for v in json.loads(r["tr"]).values() if v))
                      for r in db.execute("SELECT rowid, * FROM cues"))
        # The index itself agrees with the rows it holds.
        db.execute("INSERT INTO cues_fts (cues_fts) VALUES ('integrity-check')")
        db.commit()
    assert got == want
    return got


def _two_owners():
    # A bystander owner, so a write that touches the wrong owner's rows shows.
    store.replace_cues("other", [{"start": 0, "text": "bystander", "translations": {"g": "구경꾼"}}])
    for i in (1, 2, 3):
        store.save_cue("s", {"id": i, "kind": "final", "t": i, "text": f"line{i} hello"})


def test_fts_rows_follow_live_writes():
    _two_owners()
    _fts_matches_a_rebuild()
    store.save_translation("s", 2, "g", "번역 둘")
    store.save_translation("s", 2, "m", "second")
    _fts_matches_a_rebuild()
    # A refined line keeps its rowid and clears its translation.
    store.save_cue("s", {"id": 2, "kind": "refine", "t": 2, "text": "refined zebra"})
    rows = _fts_matches_a_rebuild()
    assert [r for r in rows if r[1] == "s" and r[2] == 2][0][3:] == ("refined zebra", "")
    store.drop_cues("s", [1, 3])
    assert len(_fts_matches_a_rebuild()) == 2
    assert store.search("zebra") and not store.search("line1")
    assert store.search("bystander")


def test_fts_rows_follow_edits_inserts_and_deletes():
    _two_owners()
    assert store.update_cue("s", 1, text="quokka")
    _fts_matches_a_rebuild()
    assert store.update_cue("s", 1, translations={"g": "쿼카"})
    _fts_matches_a_rebuild()
    assert store.update_cue("s", 1, start=9.0)          # timing only
    _fts_matches_a_rebuild()
    store.edit_cue("s", 2, text="wombat")
    _fts_matches_a_rebuild()
    store.edit_cue("s", 2, tr="웜뱃", backend="g")
    _fts_matches_a_rebuild()
    store.edit_cue("s", 2, start=5.0)
    _fts_matches_a_rebuild()
    new = store.insert_cue("s", 4.0, "platypus", tr="오리너구리")
    _fts_matches_a_rebuild()
    assert store.delete_cue("s", 3)
    _fts_matches_a_rebuild()
    assert store.search("quokka") and store.search("wombat") and store.search("platypus")
    assert not store.search("line3")
    assert new["id"] == 4


def test_fts_rows_follow_whole_owner_writes():
    _two_owners()
    store.replace_cues("s", [{"start": 0, "text": "fresh one"}, {"start": 1, "text": "fresh two"}])
    _fts_matches_a_rebuild()
    assert not store.search("hello") and store.search("fresh")
    store.save_session({"id": "s", "state": "stopped"})
    assert store.delete_session("s")
    _fts_matches_a_rebuild()
    store.save_doc("other", {"title": "x"})
    store.delete_doc("other")
    assert _fts_matches_a_rebuild() == []


def test_start_up_rebuild_repairs_a_stale_row():
    # The case 2a03d93 reproduced: the cue changed and its FTS row did not, and
    # the row counts still agree. Only the unconditional rebuild catches it.
    store.save_cue("s", {"id": 1, "kind": "final", "t": 0, "text": "hello world"})
    with store._lock:
        db = store._connect()
        db.execute("UPDATE cues SET text = 'zebra quokka' WHERE owner = 's'")
        db.commit()
    assert store.search("hello") and not store.search("zebra")    # stale
    store.init()
    assert store.search("zebra") and not store.search("hello")
    _fts_matches_a_rebuild()


# ---- The document view -----------------------------------------------------

def test_outline_round_trip():
    assert store.outline("s1") is None            # nothing written for it yet
    doc = {"sections": [{"title": "여는 말", "bullets": ["하나"], "t": 12.0}],
           "lines": 4, "chars": 90, "folded": 7, "engine": "local-gemma"}
    store.save_outline("s1", doc)
    assert store.outline("s1") == doc
    # One row per owner -- a pass rewrites the document rather than appending one.
    store.save_outline("s1", {**doc, "lines": 9})
    assert store.outline("s1")["lines"] == 9
    store.delete_outline("s1")
    assert store.outline("s1") is None
    store.delete_outline("s1")                    # deleting what is not there is not an error


def test_deleting_a_session_takes_its_document_with_it():
    # The document is filed under the same id a subtitle is, so leaving it
    # behind would hand the next session with that id somebody else's notes.
    store.save_session({"id": "s1", "state": "stopped"})
    store.save_outline("s1", {"sections": [{"title": "A", "bullets": [], "t": 0}]})
    assert store.delete_session("s1") is True
    assert store.outline("s1") is None


def test_deleting_a_recording_takes_its_document_with_it():
    store.save_doc("v9", {"title": "Video B"})
    store.save_outline("v9", {"sections": [{"title": "A", "bullets": [], "t": 0}]})
    store.delete_doc("v9")
    assert store.outline("v9") is None


# ---- Search: trigram, and LIKE under three characters -------------------------

def _search_corpus():
    store.replace_cues("jp", [
        {"start": 1, "text": "今日はみんなで配信を見ていきましょう", "translations": {"g": "오늘은 다 같이 방송을 봅시다"}},
        {"start": 2, "text": "本当にありがとうございました", "translations": {"g": "Thank you so much"}},
        {"start": 3, "text": "歌います", "translations": {"g": "노래합니다"}},
        {"start": 4, "text": "100% 本気、under_score", "translations": {}},
    ])


def test_search_finds_japanese_inside_a_sentence():
    # Under unicode61 the whole sentence was one token, so this found nothing.
    _search_corpus()
    got = store.search("ありがとう")
    assert [r["start"] for r in got] == [2]
    assert got[0]["snip"] == "本当に«ありがとう»ございました"


def test_search_short_terms_go_to_like():
    _search_corpus()
    assert [r["start"] for r in store.search("配信")] == [1]      # 2 chars, Japanese
    assert [r["start"] for r in store.search("歌")] == [3]        # 1 char
    hit = store.search("방송")                                    # 2 chars, in the translation
    assert [r["start"] for r in hit] == [1]
    assert "«방송»" in hit[0]["snip_tr"] and "«" not in hit[0]["snip"]


def test_search_korean_and_english_on_the_trigram_path():
    _search_corpus()
    assert [r["start"] for r in store.search("노래합니다")] == [3]
    got = store.search("THANK")                                   # case-insensitive
    assert [r["start"] for r in got] == [2] and got[0]["snip_tr"] == "«Thank» you so much"


def test_search_mixed_query_ands_every_term():
    # One term under three characters sends the whole query to LIKE, and the
    # terms still stand in AND, source or translation.
    _search_corpus()
    got = store.search("みんなで 방송")
    assert [r["start"] for r in got] == [1]
    assert "«みんなで»" in got[0]["snip"] and "«방송»" in got[0]["snip_tr"]
    assert store.search("ありがとう 歌") == []


def test_search_escapes_like_wildcards():
    _search_corpus()
    assert [r["start"] for r in store.search("0%")] == [4]         # not "anything after 0"
    assert [r["start"] for r in store.search("%")] == [4]
    assert store.search("_") and [r["start"] for r in store.search("r_")] == [4]
    assert store.search("\\") == [] and store.search('"') == []


def test_snippet_cuts_long_lines_and_marks_every_term():
    long = "あ" * 60 + "配信" + "い" * 60
    s = store._snippet(long, ["配信"])
    assert s.startswith("…") and s.endswith("…") and "«配信»" in s
    assert len(s) <= store.SNIPPET_CHARS + 4
    assert store._snippet("a b a", ["a"]) == "«a» b «a»"
    assert store._snippet("no hit here", ["zzz"]) == "no hit here"


def test_unicode61_table_is_migrated_at_start():
    # A database made before the tokenizer changed: IF NOT EXISTS alone would
    # keep its unicode61 table for good.
    store.save_cue("s", {"id": 1, "kind": "final", "t": 0, "text": "本当にありがとうございました"})
    with store._lock:
        db = store._connect()
        db.execute("DROP TABLE cues_fts")
        db.execute("CREATE VIRTUAL TABLE cues_fts USING fts5("
                   "owner UNINDEXED, cue_id UNINDEXED, text, tr_text)")
        db.execute("INSERT INTO cues_fts (rowid, owner, cue_id, text, tr_text) "
                   "SELECT rowid, owner, cue_id, text, '' FROM cues")
        db.commit()
    assert store.search("ありがとう") == []                      # the old tokenizer
    _restart()
    sql = store._connect().execute(
        "SELECT sql FROM sqlite_master WHERE name = 'cues_fts'").fetchone()[0]
    assert "trigram" in sql
    assert store.search("ありがとう")
    _fts_matches_a_rebuild()
    # Already migrated: a second start leaves the table alone.
    assert store._fts_migrate(store._connect()) is False


def test_a_database_without_the_fts_table_gets_one():
    store.save_cue("s", {"id": 1, "kind": "final", "t": 0, "text": "ありがとう"})
    with store._lock:
        store._connect().execute("DROP TABLE cues_fts")
        store._connect().commit()
    _restart()
    assert store.search("ありがとう")
    _fts_matches_a_rebuild()


def _restart():
    """What a process start does: a new connection, then init()."""
    with store._lock:
        store._db.close()
        store._db = None
    store.init()
