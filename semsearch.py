"""Search the subtitle library by meaning, beside the full-text search.

The full-text search (store.search, section 55) finds the letters that were
typed: a word in the source line or in its translation. It cannot find a line
that says the same thing in other words, and a viewer looking for "the part
where she talked about her cat" rarely remembers the words. This module ranks
cues by how close their meaning is to the query instead.

**The model** is EmbeddingGemma 2, text encoder only (271M parameters), as the
int8 ONNX export (`onnx-community/embeddinggemma-2-ONNX`, 314MB of weights,
346MB with the tokenizer), run by
onnxruntime with the `tokenizers` package. It was chosen over the two other
ways to run it. llama-cpp-python, which already runs Gemma here, does not know
the architecture yet: its newest release (0.3.36) vendors a llama.cpp 140
commits older than the one that added it (PR #30054). sentence-transformers
does run it, but pulls in torch (590MB installed) for what onnxruntime (80MB)
does with the same answer -- on 400 real cues the int8 ONNX embedding and the
sentence-transformers one have cosine 1.0000 on average (lowest 0.9997) and the
same top 10 for 98.5% of the results. The q4 export was dropped: 0.987 on
average, 81% of the same top 10, and slower. Measurements in section 57.

**Only the source text is embedded**, not the translations. The model puts
Korean, Japanese and English into one space, so a Korean query reaches a
Japanese line without going through its translation, and a retranslation does
not make the index stale.

**Nothing is indexed before it is asked for.** The first meaning search starts
one background pass over every cue not yet embedded, and the search answers
from whatever is indexed so far; later searches catch up with the lines added
since, which is a handful. A pass never starts by itself -- only a search
starts one. A meaning search made while a live session is transcribing does
start one, and for its length the pass shares the CPU with the transcriber;
section 57 has how long a whole-store pass takes. The model loads on that first
search through `models.shared` like every other model, and is let go by the
same idle rule.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time

import numpy as np

import bus
import models
import paths
import store

MODEL_ID = "embeddinggemma-2"            # modelhub catalogue id
MODEL_DIR = "embeddinggemma-2-onnx"      # under paths.model_dir()
ONNX_FILE = "onnx/model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"

# The task prefixes the model card gives for retrieval. They are part of what the
# model was trained on; a text-to-text search without them is not the documented use.
QUERY_PREFIX = "task: search result | query: "
DOC_PREFIX = "title: none | text: "

# How many cues one embedding covers: the cue and the ones after it in the same
# owner. A cue is often a few words, so joining the next two looked like it would
# give the model a sentence to work with. It did the opposite: on a known-item
# search over the real store (28,836 cues, a cue's Korean translation as the
# query) the cue came back in the top 10 for 82% of queries one cue at a time,
# 64% with a window of three (counting any window holding the cue) and 55% with
# five, and the pass took 4.0 min instead of 6.9 and 8.8. Section 57. A query
# about a whole passage rather than one line was not measured.
WINDOW = 1
# Matryoshka dimensions kept. 768 found the cue in the top 10 for 82%, 256 for
# 79%, 128 for 74%; 256 keeps the table and the matrix at a third (29.5MB for the
# store instead of 88.6MB). Section 57.
DIM = 256
# The windows are short; a long VOD cue joined with two more stays far below
# this. It bounds the cost of one pathological line, nothing else.
MAX_TOKENS = 256
BATCH = 32
# onnxruntime threads for indexing and queries. 2 / 4 / 8 / 12 threads index the
# store in 24.6 / 17.1 / 14.9 / 15.6 min (window of three). Past 4 the gain is
# 13%, and a pass may share the machine with a live transcriber. Section 57.
THREADS = 4
# How often a running pass reports its progress on the change feed.
PROGRESS_EVERY_S = 1.0


def model_path() -> str:
    return os.path.join(paths.model_dir(), MODEL_DIR)


def unavailable() -> str | None:
    """Why a meaning search cannot run here, or None when it can.
    "packages" -- onnxruntime or tokenizers is not installed (the install script
    puts them in; an older virtualenv lacks them). "model" -- the model has not
    been downloaded from "Models & Tools"."""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return "packages"
    base = model_path()
    for f in (ONNX_FILE, ONNX_FILE + "_data", TOKENIZER_FILE):
        if not os.path.isfile(os.path.join(base, f)):
            return "model"
    if os.path.exists(os.path.join(base, ".incomplete")):
        return "model"
    return None


class Embedder:
    """The ONNX text encoder. `embed` returns L2-normalised rows cut to DIM
    (Matryoshka: the first DIM components, normalised again)."""

    def __init__(self, base: str, threads: int = THREADS):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(os.path.join(base, ONNX_FILE), opts,
                                            providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(os.path.join(base, TOKENIZER_FILE))
        self.tok.enable_truncation(MAX_TOKENS)
        # The export takes the media features as inputs too; a text-only call
        # passes them empty.
        self.empty = {i.name: np.zeros((0, 512), np.float32)
                      for i in self.session.get_inputs()
                      if i.name not in ("input_ids", "attention_mask")}

    def embed(self, texts: list[str], prefix: str, dim: int | None = None) -> np.ndarray:
        enc = self.tok.encode_batch([prefix + t for t in texts])
        n = max(len(e.ids) for e in enc)
        ids = np.zeros((len(enc), n), np.int64)
        mask = np.zeros((len(enc), n), np.int64)
        for i, e in enumerate(enc):
            ids[i, :len(e.ids)] = e.ids
            mask[i, :len(e.ids)] = 1
        out = self.session.run(["sentence_embedding"],
                               {"input_ids": ids, "attention_mask": mask, **self.empty})[0]
        return _cut(out, dim or DIM)


def _cut(v: np.ndarray, dim: int) -> np.ndarray:
    v = np.asarray(v, np.float32)[:, :dim]
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


# What builds the embedder. Tests replace this with a deterministic fake --
# tests never load a model.
make_embedder = Embedder


def embedder():
    key = ("embed", model_path())
    got = models.shared(key, lambda: make_embedder(model_path()))
    models.touch(key)
    return got


# ---- Windows ----------------------------------------------------------------

def windows(rows, size: int | None = None) -> list[tuple[str, int, str]]:
    """(owner, cue_id, text) for every cue: its text joined with the next
    size-1 cues of the same owner (WINDOW when not given, read at call time).
    `rows` are in reading order (owner, start)."""
    size = size or WINDOW
    out = []
    n = len(rows)
    for i, r in enumerate(rows):
        parts = [r["text"]]
        j = i + 1
        while j < n and j < i + size and rows[j]["owner"] == r["owner"]:
            parts.append(rows[j]["text"])
            j += 1
        text = " ".join(p for p in parts if p).strip()
        out.append((r["owner"], r["cue_id"], text))
    return out


def _hash(text: str) -> str:
    # The settings that shape a vector go into the hash too, so changing the
    # window or the dimension re-indexes rather than mixing two kinds of vector.
    return hashlib.blake2b(f"{WINDOW}:{DIM}:{text}".encode(), digest_size=8).hexdigest()


# ---- The indexing pass ------------------------------------------------------

_lock = threading.Lock()
_state = {"running": False, "done": 0, "total": 0, "error": None}
_version = 0                          # bumped whenever cue_vec changes
_cache = {"version": -1, "keys": [], "mat": None}


def status() -> dict:
    with _lock:
        return dict(_state)


def _due() -> list[tuple[str, int, str, str]]:
    """(owner, cue_id, hash, text) for every cue whose embedding is missing or
    stale. Drops the vectors of cues that no longer exist on the way."""
    global _version
    have = store.vec_hashes()
    due, keep = [], set()
    for owner, cue_id, text in windows(store.cue_texts()):
        key = (owner, cue_id)
        if not text:
            continue                  # nothing to find it by; an old vector is pruned below
        keep.add(key)
        h = _hash(text)
        if have.get(key) != h:
            due.append((owner, cue_id, h, text))
    gone = [k for k in have if k not in keep]
    if gone:
        store.vec_drop(gone)
        with _lock:
            _version += 1
    return due


def ensure_index() -> dict:
    """Start a pass if anything is due and none is running. Returns the status."""
    with _lock:
        if _state["running"]:
            return dict(_state)
    due = _due()
    with _lock:
        if _state["running"] or not due:
            return dict(_state)
        _state.update(running=True, done=0, total=len(due), error=None)
    threading.Thread(target=_run, args=(due,), name="semsearch-index", daemon=True).start()
    return status()


def _run(due):
    global _version
    last = 0.0
    try:
        emb = embedder()
        for i in range(0, len(due), BATCH):
            chunk = due[i:i + BATCH]
            vecs = emb.embed([t for *_, t in chunk], DOC_PREFIX)
            store.vec_put([(o, c, h, v.astype(np.float32).tobytes())
                           for (o, c, h, _), v in zip(chunk, vecs)])
            with _lock:
                _state["done"] = i + len(chunk)
                _version += 1
            models.touch(("embed", model_path()))
            if time.time() - last >= PROGRESS_EVERY_S:
                last = time.time()
                bus.publish({"type": "search", **status()})
    except Exception as e:                # a pass that dies says why, and the next search retries
        with _lock:
            _state["error"] = str(e)
    finally:
        with _lock:
            _state["running"] = False
        bus.publish({"type": "search", **status()})


def _matrix() -> tuple[list[tuple[str, int]], np.ndarray | None]:
    with _lock:
        if _cache["version"] == _version and _cache["mat"] is not None:
            return _cache["keys"], _cache["mat"]
        version = _version
    rows = store.vec_all()
    keys = [(r["owner"], r["cue_id"]) for r in rows]
    mat = (np.frombuffer(b"".join(r["v"] for r in rows), np.float32).reshape(len(rows), -1)
           if rows else None)
    with _lock:
        _cache.update(version=version, keys=keys, mat=mat)
    return keys, mat


def search(q: str, limit: int = 50) -> dict:
    """{"results": [...], "index": status} -- or {"unavailable": reason}.

    Results are cues ranked by cosine similarity, with the score. With a
    WINDOW over 1 a cue is returned for its window, so a hit on words a line
    later opens at the line where the passage begins."""
    reason = unavailable()
    if reason:
        return {"unavailable": reason, "results": []}
    state = ensure_index()
    keys, mat = _matrix()
    if mat is None or not (q or "").strip():
        return {"results": [], "index": state}
    try:
        qv = embedder().embed([q.strip()], QUERY_PREFIX)[0]
    except Exception as e:
        # A model that fails to load would otherwise take the request down with
        # it (the handler has no answer to give); the page shows this under the box.
        return {"results": [], "index": {**state, "error": str(e)}}
    scores = mat @ qv
    k = min(limit, len(keys))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    found = store.cues_at([keys[i] for i in top])
    results = []
    for i in top:
        r = found.get(keys[i])
        if r is None:
            continue
        results.append({"owner": r["owner"], "cue_id": r["cue_id"], "start": r["start"],
                        "text": r["text"], "tr_text": store.tr_text(r["tr"]),
                        "score": round(float(scores[i]), 4)})
    return {"results": results, "index": state}
