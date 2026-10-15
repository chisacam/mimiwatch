"""The meaning search (semsearch.py): the windows, the indexing pass, the ranking.

No model is loaded. EmbeddingGemma is swapped for a deterministic stand-in
that hashes character trigrams into a vector -- identical texts get identical
vectors and texts that share words land close -- through the module's own seam,
`semsearch.make_embedder`. Every test points the model directory at its own
temporary one: the real one is where the owner downloads the model, and a test
that read it would load that model the day it appeared there.

The pass is run in this thread (`_run(_due())`) wherever the thread itself is
not what is under test. Where it is, the thread is joined before the test ends:
`isolated` closes the store connection on the way out, and a pass still writing
then would reopen the store at whatever path monkeypatch had put back -- the
real one.
"""
import json
import sys
import threading
import zlib

import numpy as np
import pytest

import bus
import models
import semsearch
import store


class FakeEmbedder:
    """Character trigrams hashed into `dim` buckets and L2-normalised. The prefix
    is recorded and otherwise ignored, so a query equal to a window's text scores
    exactly 1.0 against it."""

    def __init__(self, base):
        self.base = base
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts, prefix, dim=None):
        self.calls.append((list(texts), prefix))
        n = dim or semsearch.DIM
        out = np.zeros((len(texts), n), np.float32)
        for i, t in enumerate(texts):
            s = f"  {t.lower()} "
            for j in range(len(s) - 2):
                out[i, zlib.crc32(s[j:j + 3].encode()) % n] += 1.0
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)


def _model_files(mdir):
    base = mdir / semsearch.MODEL_DIR
    for f in (semsearch.ONNX_FILE, semsearch.ONNX_FILE + "_data", semsearch.TOKENIZER_FILE):
        (base / f).parent.mkdir(parents=True, exist_ok=True)
        (base / f).write_bytes(b"")
    return base


def _join_passes(timeout=5.0):
    for t in threading.enumerate():
        if t.name == "semsearch-index":
            t.join(timeout)
    assert not [t for t in threading.enumerate() if t.name == "semsearch-index"], \
        "an indexing pass outlived the test"


@pytest.fixture(autouse=True)
def sem(isolated, tmp_path, monkeypatch):
    """A model directory of the test's own, and the module's state as at import."""
    mdir = tmp_path / "models"
    monkeypatch.setenv("MIMIWATCH_MODEL_DIR", str(mdir))
    monkeypatch.setattr(semsearch, "_state",
                        {"running": False, "done": 0, "total": 0, "error": None})
    monkeypatch.setattr(semsearch, "_version", 0)
    monkeypatch.setattr(semsearch, "_cache", {"version": -1, "keys": [], "mat": None})
    models.clear()
    yield mdir
    _join_passes()
    models.clear()


@pytest.fixture
def fake(sem, monkeypatch):
    """The stand-in embedder, and the model files present so the real
    `unavailable()` lets the search run. Returns the embedders built."""
    made: list[FakeEmbedder] = []

    def make(base):
        e = FakeEmbedder(base)
        made.append(e)
        return e

    monkeypatch.setattr(semsearch, "make_embedder", make)
    _model_files(sem)
    assert semsearch.unavailable() is None
    return made


LINES = ["the cat sleeps on the sofa", "rain falls on tokyo tonight",
         "we ate ramen after the show", "my keyboard is broken again",
         "see you next stream", "thanks for the raid everyone"]


def _video(owner="v1", lines=LINES, translations=None):
    store.replace_cues(owner, [{"start": float(i * 10), "end": float(i * 10 + 5), "text": t,
                                "translations": (translations or {}).get(i + 1) or {}}
                               for i, t in enumerate(lines)])


def _window(owner, cue_id):
    return next(t for o, c, t in semsearch.windows(store.cue_texts())
                if (o, c) == (owner, cue_id))


def _keys(due):
    return {(o, c) for o, c, *_ in due}


def _index():
    semsearch._run(semsearch._due())
    assert semsearch.status()["error"] is None
    assert semsearch._due() == []


# ---- The embedder, without its model ---------------------------------------

def test_embed_pads_the_batch_and_cuts_to_the_kept_dimensions():
    """Embedder.embed with a stand-in tokenizer and session: everything it does
    around the model is checked, the model itself is not there."""
    class Enc:
        def __init__(self, ids):
            self.ids = ids

    class Tok:
        def encode_batch(self, texts):
            self.seen = texts
            return [Enc(list(range(1, len(t.split()) + 1))) for t in texts]

    class Session:
        def run(self, names, feeds):
            self.names, self.feeds = names, feeds
            out = np.zeros((len(feeds["input_ids"]), 768), np.float32)
            out[:, 0], out[:, 1], out[:, 600] = 3.0, 4.0, 100.0   # past DIM: cut off
            return [out]

    e = object.__new__(semsearch.Embedder)
    e.tok, e.session = Tok(), Session()
    e.empty = {"pixel_values": np.zeros((0, 512), np.float32)}
    got = e.embed(["one two three", "four"], "p: ")

    assert e.tok.seen == ["p: one two three", "p: four"]
    assert e.session.names == ["sentence_embedding"]
    feeds = e.session.feeds
    assert feeds["input_ids"].tolist() == [[1, 2, 3, 4], [1, 2, 0, 0]]
    assert feeds["attention_mask"].tolist() == [[1, 1, 1, 1], [1, 1, 0, 0]]
    assert feeds["pixel_values"].shape == (0, 512)
    assert got.shape == (2, semsearch.DIM) and got.dtype == np.float32
    assert np.allclose(got[:, :2], [[0.6, 0.8], [0.6, 0.8]]) and not got[:, 2:].any()
    assert e.embed(["x"], "", dim=1).tolist() == [[1.0]]


# ---- Windows ----------------------------------------------------------------

def test_windows_join_within_an_owner_and_never_across():
    rows = [{"owner": "a", "cue_id": 1, "text": "one"},
            {"owner": "a", "cue_id": 2, "text": "two"},
            {"owner": "a", "cue_id": 3, "text": ""},
            {"owner": "a", "cue_id": 4, "text": "four"},
            {"owner": "b", "cue_id": 1, "text": "bee"},
            {"owner": "b", "cue_id": 2, "text": ""}]
    assert semsearch.windows(rows, 3) == [
        ("a", 1, "one two"),            # the empty third cue adds no stray space
        ("a", 2, "two four"),
        ("a", 3, "four"),               # stops at the owner's end, never reaches "bee"
        ("a", 4, "four"),
        ("b", 1, "bee"),
        ("b", 2, ""),                   # nothing to embed: _due skips it
    ]
    assert semsearch.windows(rows, 1) == [(r["owner"], r["cue_id"], r["text"]) for r in rows]
    assert semsearch.windows(rows) == semsearch.windows(rows, semsearch.WINDOW)
    assert semsearch.windows([]) == []


def test_windows_follow_time_not_the_cue_number(monkeypatch):
    # A line a person writes in takes the next number but sits where its time is;
    # the window has to read on in time, as the panel does.
    monkeypatch.setattr(semsearch, "WINDOW", 3)
    store.replace_cues("v", [{"start": 0.0, "text": "alpha"}, {"start": 10.0, "text": "gamma"}])
    beta = store.insert_cue("v", 5.0, "beta")
    assert beta["id"] == 3
    assert semsearch.windows(store.cue_texts()) == [
        ("v", 1, "alpha beta gamma"), ("v", 3, "beta gamma"), ("v", 2, "gamma")]


# ---- The indexing pass ------------------------------------------------------

def test_first_pass_embeds_every_cue_and_the_next_finds_nothing_due(fake):
    _video()
    store.save_cue("s1", {"id": 1, "kind": "final", "t": 0, "text": "hello there"})
    store.save_cue("s1", {"id": 2, "kind": "final", "t": 1, "text": "general kenobi"})
    every = {("v1", i) for i in range(1, len(LINES) + 1)} | {("s1", 1), ("s1", 2)}

    due = semsearch._due()
    assert _keys(due) == every
    semsearch._run(due)
    assert set(store.vec_hashes()) == every
    assert all(len(r["v"]) == semsearch.DIM * 4 for r in store.vec_all())   # float32
    assert semsearch._due() == []
    # One model for the whole pass, every call with the document prefix.
    assert len(fake) == 1
    assert {p for _, p in fake[0].calls} == {semsearch.DOC_PREFIX}
    assert sum(len(t) for t, _ in fake[0].calls) == len(every)


def test_an_edit_makes_due_exactly_the_windows_holding_the_cue(fake, monkeypatch):
    # The default is one cue per vector (section 57); the window logic is kept
    # and checked at three, where an edit reaches back across owners' lines.
    monkeypatch.setattr(semsearch, "WINDOW", 3)
    _video()
    _video("v2", ["another owner", "left alone"])
    _index()

    # WINDOW = 3: the edited cue and the two before it in the same owner.
    store.edit_cue("v1", 4, text="my mouse is broken again")
    assert _keys(semsearch._due()) == {("v1", 2), ("v1", 3), ("v1", 4)}
    _index()

    store.edit_cue("v1", 1, text="the dog sleeps on the sofa")
    assert _keys(semsearch._due()) == {("v1", 1)}
    _index()

    # The live path: a refined line replaces a final under the same id.
    store.save_cue("v1", {"id": 6, "kind": "refined", "t": 50.0,
                          "text": "thanks for the raid, everyone"})
    assert _keys(semsearch._due()) == {("v1", 4), ("v1", 5), ("v1", 6)}
    _index()

    # Only the source is embedded: a translation or a timing change is nothing to redo.
    store.edit_cue("v1", 3, tr="라멘을 먹었다", backend="g")
    store.edit_cue("v1", 3, start=21.0)
    assert semsearch._due() == []


def test_changing_the_window_or_dimension_makes_every_cue_due(fake, monkeypatch):
    # The hash carries the settings, so two kinds of vector never mix in one matrix.
    _video()
    _index()
    monkeypatch.setattr(semsearch, "DIM", semsearch.DIM // 2)
    assert len(semsearch._due()) == len(LINES)
    monkeypatch.setattr(semsearch, "DIM", semsearch.DIM * 2)
    monkeypatch.setattr(semsearch, "WINDOW", semsearch.WINDOW + 1)
    assert len(semsearch._due()) == len(LINES)


def test_a_deleted_cue_loses_its_vector_and_is_never_returned(fake, monkeypatch):
    monkeypatch.setattr(semsearch, "WINDOW", 3)
    _video()
    _index()
    gone_text = _window("v1", 3)
    v0 = semsearch._version

    store.delete_cue("v1", 3)
    due = semsearch._due()
    assert ("v1", 3) not in store.vec_hashes()
    assert semsearch._version > v0                     # the cached matrix is stale now
    assert _keys(due) == {("v1", 1), ("v1", 2)}        # the windows that read past it
    semsearch._run(due)

    got = semsearch.search(gone_text)
    assert got["results"]
    assert ("v1", 3) not in {(r["owner"], r["cue_id"]) for r in got["results"]}


def test_a_cue_emptied_by_an_edit_loses_its_vector(fake):
    # A window that becomes empty used to be kept and skipped: its old vector
    # was never re-embedded nor pruned, and a search for the old words returned
    # a line with no text at score 1.0. post_cue accepts "" for the text.
    _video(lines=LINES[:3])
    _index()
    old = _window("v1", 3)
    store.edit_cue("v1", 3, text="")
    semsearch._due()
    assert ("v1", 3) not in store.vec_hashes()
    semsearch._run(semsearch._due())
    got = semsearch.search(old)
    assert ("v1", 3) not in {(r["owner"], r["cue_id"]) for r in got["results"]}


def test_a_cue_deleted_during_a_pass_is_not_returned_though_its_vector_remains(fake):
    # While a pass runs, a search does not prune (ensure_index returns early), so
    # the vector is still in the matrix -- the cue lookup is what keeps it out.
    _video()
    _index()
    q = _window("v1", 2)
    store.delete_cue("v1", 2)
    semsearch._state["running"] = True
    try:
        got = semsearch.search(q)
    finally:
        semsearch._state["running"] = False
    assert ("v1", 2) in store.vec_hashes()
    keys = [(r["owner"], r["cue_id"]) for r in got["results"]]
    assert ("v1", 2) not in keys and len(keys) == len(LINES) - 1


def test_a_failing_model_ends_the_pass_with_the_reason(sem):
    # No stand-in: the real Embedder is built, and conftest's guard over
    # onnxruntime is what stops it. The pass must end cleanly and say why, and
    # the next search must find the same cues still due.
    _model_files(sem)
    _video()
    with pytest.raises(RuntimeError, match="tests do not load models"):
        semsearch.embedder()
    q = bus.subscribe()
    try:
        semsearch._run(semsearch._due())
        events = [json.loads(q.get_nowait()) for _ in range(q.qsize())]
    finally:
        bus.unsubscribe(q)
    st = semsearch.status()
    assert st["running"] is False and "tests do not load models" in st["error"]
    assert store.vec_hashes() == {}
    assert len(semsearch._due()) == len(LINES)
    last = [e for e in events if e.get("type") == "search"][-1]
    assert last["running"] is False and "tests do not load models" in last["error"]


def test_ensure_index_runs_one_pass_on_a_thread(fake, monkeypatch):
    _video()
    gate = threading.Event()
    real_embed = FakeEmbedder.embed

    def held(self, texts, prefix, dim=None):
        assert gate.wait(5)
        return real_embed(self, texts, prefix, dim)

    monkeypatch.setattr(FakeEmbedder, "embed", held)
    try:
        st = semsearch.ensure_index()
        assert st["total"] == len(LINES)
        assert semsearch.status()["running"] is True
        # A second call while it runs starts nothing.
        assert semsearch.ensure_index()["running"] is True
        assert len([t for t in threading.enumerate() if t.name == "semsearch-index"]) == 1
    finally:
        gate.set()
    _join_passes()
    assert semsearch.status() == {"running": False, "done": len(LINES),
                                  "total": len(LINES), "error": None}
    assert semsearch._due() == []
    # Nothing due: no thread.
    assert semsearch.ensure_index()["running"] is False
    assert not [t for t in threading.enumerate() if t.name == "semsearch-index"]


def test_a_pass_reports_its_end_on_the_bus(fake):
    _video()
    q = bus.subscribe()
    try:
        semsearch.ensure_index()
        _join_passes()
        events = [json.loads(q.get_nowait()) for _ in range(q.qsize())]
    finally:
        bus.unsubscribe(q)
    mine = [e for e in events if e.get("type") == "search"]
    assert mine, events
    assert mine[-1] == {"type": "search", "running": False, "done": len(LINES),
                        "total": len(LINES), "error": None}


# ---- Searching --------------------------------------------------------------

def test_search_ranks_the_matching_window_first(fake):
    _video(translations={2: {"g": "오늘 밤 도쿄에 비"}})
    _video("v2", ["completely unrelated words here", "nothing about weather"])
    _index()
    q = _window("v1", 2)

    got = semsearch.search("  " + q + " ")
    assert got["index"] == semsearch.status()
    top = got["results"][0]
    assert set(top) == {"owner", "cue_id", "start", "text", "tr_text", "score"}
    # The cue opens where its passage begins; the text is the cue's own, not the window.
    assert (top["owner"], top["cue_id"]) == ("v1", 2)
    assert top["start"] == 10.0 and top["text"] == LINES[1]
    assert top["tr_text"] == "오늘 밤 도쿄에 비"
    assert top["score"] == 1.0
    scores = [r["score"] for r in got["results"]]
    assert scores == sorted(scores, reverse=True)
    assert len(scores) == len(LINES) + 2
    # The query goes in with the query prefix, stripped.
    assert fake[0].calls[-1] == ([q], semsearch.QUERY_PREFIX)

    assert len(semsearch.search(q, limit=2)["results"]) == 2
    n = len(fake[0].calls)
    assert semsearch.search("   ") == {"results": [], "index": semsearch.status()}
    assert len(fake[0].calls) == n                     # an empty query embeds nothing


def test_search_with_nothing_indexed_answers_empty(fake):
    assert semsearch.search("anything") == {"results": [], "index": semsearch.status()}
    assert fake == []                                  # no model built for an empty library


def test_a_new_cue_is_found_once_a_pass_moves_the_version(fake):
    _video()
    _index()
    semsearch.search("cat")
    assert semsearch._cache["version"] == semsearch._version
    keys, mat = semsearch._matrix()
    assert semsearch._matrix()[1] is mat               # unchanged version: the same matrix

    new = store.insert_cue("v1", 99.0, "a volcano erupted during the stream")
    v = semsearch._version
    semsearch._run(semsearch._due())
    assert semsearch._version > v

    got = semsearch.search("a volcano erupted during the stream")
    assert (got["results"][0]["cue_id"], got["results"][0]["score"]) == (new["id"], 1.0)
    assert semsearch._cache["version"] == semsearch._version
    assert ("v1", new["id"]) in semsearch._cache["keys"]


# ---- Availability -----------------------------------------------------------

def test_unavailable_reads_the_model_files(sem):
    assert semsearch.unavailable() == "model"          # nothing downloaded
    base = sem / semsearch.MODEL_DIR
    for f in (semsearch.ONNX_FILE, semsearch.ONNX_FILE + "_data", semsearch.TOKENIZER_FILE):
        assert semsearch.unavailable() == "model"
        (base / f).parent.mkdir(parents=True, exist_ok=True)
        (base / f).write_bytes(b"")
    assert semsearch.unavailable() is None
    # A download that has not finished yet leaves its marker.
    (base / ".incomplete").write_bytes(b"")
    assert semsearch.unavailable() == "model"
    (base / ".incomplete").unlink()
    (base / semsearch.TOKENIZER_FILE).unlink()
    assert semsearch.unavailable() == "model"


@pytest.mark.parametrize("missing", ["onnxruntime", "tokenizers"])
def test_unavailable_says_packages_without_the_runtime(sem, monkeypatch, missing):
    _model_files(sem)
    monkeypatch.setitem(sys.modules, missing, None)    # the import raises ImportError
    assert semsearch.unavailable() == "packages"


def test_search_when_unavailable_starts_no_pass(sem, monkeypatch):
    built = []
    monkeypatch.setattr(semsearch, "make_embedder", lambda base: built.append(base))
    _video()
    assert semsearch.search("cat") == {"unavailable": "model", "results": []}
    assert built == []
    assert not [t for t in threading.enumerate() if t.name == "semsearch-index"]
    assert semsearch.status() == {"running": False, "done": 0, "total": 0, "error": None}
    assert store.vec_hashes() == {}


# ---- The server's half ------------------------------------------------------

def test_meaning_search_is_shaped_for_the_screen(fake, sem):
    import server as srv

    store.save_session({"id": "s1", "state": "stopped", "title": "Night stream"})
    store.save_cue("s1", {"id": 1, "kind": "final", "t": 3.0, "text": "the moon is bright"})
    store.save_doc("v1", {"id": "v1", "title": "Video one"})
    _video()
    _index()

    got = srv.Handler._meaning_search(None, "the moon is bright")
    assert set(got) == {"q", "mode", "results", "index"} and got["mode"] == "meaning"
    top = got["results"][0]
    assert top == {"value": "live:s1", "title": "Night stream", "start": 3.0,
                   "text": "the moon is bright", "tr_text": "", "score": 1.0}
    assert {r["value"] for r in got["results"]} == {"live:s1", "v1"}
    assert {r["title"] for r in got["results"] if r["value"] == "v1"} == {"Video one"}

    assert srv.Handler._meaning_search(None, "") == {
        "q": "", "mode": "meaning", "results": [], "index": semsearch.status()}

    (sem / semsearch.MODEL_DIR / semsearch.ONNX_FILE).unlink()
    assert srv.Handler._meaning_search(None, "moon") == {
        "q": "moon", "mode": "meaning", "unavailable": "model", "results": []}


def test_search_route_picks_the_mode_and_keeps_the_full_text_answer(fake):
    """get_search called in this process, the way test_server calls a route
    whose dependency cannot be faked across the process boundary."""
    import server as srv

    assert srv.GET_ROUTES["/api/search"] is srv.Handler.get_search
    _video()
    _index()

    class Fake:
        """Everything get_search touches on `self`."""

        sent = None
        _meaning_search = srv.Handler._meaning_search

        def __init__(self, qs):
            self.qs = qs

        def _query(self):
            return self.qs

        def _json(self, obj, code=200):
            self.sent = (code, obj)

    f = Fake({"q": "ramen", "mode": "meaning"})
    srv.Handler.get_search(f)
    assert f.sent[0] == 200 and f.sent[1]["mode"] == "meaning" and f.sent[1]["results"]

    f = Fake({"q": "ramen"})
    srv.Handler.get_search(f)
    code, body = f.sent
    assert code == 200 and set(body) == {"q", "results"}
    assert [(r["value"], r["text"]) for r in body["results"]] == [("v1", LINES[2])]
    assert {"snip", "snip_tr"} <= set(body["results"][0])
