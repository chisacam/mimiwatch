"""What the meaning search (semsearch.py) costs and how its knobs behave, over
the real subtitle store.

Three things are asked.

  [1] Cost. Milliseconds per indexed window at a few onnxruntime thread
      counts, what that makes for the whole store, the memory the model takes
      and the size of the vector table. The first meaning search indexes
      everything, so this decides whether that pass can block a request (it
      cannot) and how long the progress line stays up.

  [2] The window. A cue is often a few words; embedding it with the next one or
      two gives the model a sentence to work with but blurs which line a hit is
      on. WINDOW = 1, 3 and 5 are compared.

  [3] The dimension. Matryoshka lets the vectors be cut from 768 to 256 or 128;
      the table and the in-memory matrix shrink by the same factor.

The yardstick for [2] and [3] is a known-item search across languages. The
query is a cue's translation (M2M-100 or Gemma, whatever the store holds) in a
language other than its source; the index holds the source text only. A query
is found when the cue comes back in the top 10 -- strictly the cue itself, or
any window that contains it (the passage it is part of). This is NOT the case
the feature exists for: the full-text search indexes the translations, so it
would find every one of these queries by substring and is no control here. The
paraphrase and "what was it about" queries that are the reason to add a meaning
search are not measured by this script.

It loads a model and reads the store (read-only), so it is not a `*_check.py`
and check_all.py does not run it.

    .venv/bin/python bench/semsearch_bench.py --model <dir> [--db <path>]

`<dir>` holds onnx/model_quantized.onnx(+_data) and tokenizer.json -- the
"embeddinggemma-2-onnx" directory "Models & Tools" downloads.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import resource
import sqlite3
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import semsearch  # noqa: E402

HANGUL = re.compile(r"[가-힣]")
KANA = re.compile(r"[぀-ヿ]")


def script_lang(text: str) -> str:
    if HANGUL.search(text):
        return "ko"
    if KANA.search(text):
        return "ja"
    return "en" if re.search(r"[A-Za-z]{3}", text) else "?"


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1 << 20) if sys.platform == "darwin" else r / 1024


def load(db_path: str):
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db.execute("SELECT owner, cue_id, text, lang, tr FROM cues "
                      "ORDER BY owner, start, cue_id").fetchall()


def queries(rows, n: int, seed: int = 0):
    """(row index, query text, source lang, query lang) -- a translation whose
    script says it is in a different language from its source line."""
    out = []
    for i, r in enumerate(rows):
        if len(r["text"] or "") < 6:
            continue
        src = script_lang(r["text"])
        for t in json.loads(r["tr"] or "{}").values():
            t = str(t or "")
            ql = script_lang(t)
            if len(t) >= 6 and ql not in ("?", src):
                out.append((i, t, src, ql))
                break
    random.Random(seed).shuffle(out)
    return out[:n]


def embed_all(emb, texts, dim=768, batch=32):
    vecs = []
    for i in range(0, len(texts), batch):
        vecs.append(emb.embed(texts[i:i + batch], semsearch.DOC_PREFIX, dim=dim))
    return np.concatenate(vecs)


def evaluate(rows, mat, qmat, qs, size, k=10):
    """hit@1 / hit@10 strict, hit@10 and MRR@10 over the passage."""
    owner_of = [r["owner"] for r in rows]
    hit1 = hit10 = cover10 = 0
    rr = 0.0
    scores = qmat @ mat.T
    for (i, *_), s in zip(qs, scores):
        top = np.argpartition(-s, k)[:k]
        top = top[np.argsort(-s[top])]
        # Windows that contain cue i: those starting at i-size+1 .. i in the same owner.
        holders = {j for j in range(max(0, i - size + 1), i + 1) if owner_of[j] == owner_of[i]}
        hit1 += int(top[0] == i)
        hit10 += int(i in top)
        rank = next((n for n, j in enumerate(top) if j in holders), None)
        if rank is not None:
            cover10 += 1
            rr += 1.0 / (rank + 1)
    n = len(qs)
    return hit1 / n, hit10 / n, cover10 / n, rr / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--db", default=os.path.join(semsearch.paths.data_dir(), "mimiwatch.db"))
    ap.add_argument("--queries", type=int, default=300)
    ap.add_argument("--threads", default="2,4,8,12")
    ap.add_argument("--windows", default="1,3,5")
    ap.add_argument("--cost-sample", type=int, default=1000)
    args = ap.parse_args()

    rows = load(args.db)
    langs = {}
    for r in rows:
        langs[r["lang"]] = langs.get(r["lang"], 0) + 1
    print(f"store: {len(rows)} cues, {len({r['owner'] for r in rows})} owners, "
          f"source languages {langs}\n")

    # [1] cost
    base = rss_mb()
    t = time.time()
    semsearch.Embedder(args.model, threads=4)
    print(f"model load: {time.time() - t:.1f} s, max RSS {base:.0f} -> {rss_mb():.0f} MB\n")
    texts3 = [w[2] for w in semsearch.windows(rows, 3)]
    sample = random.Random(1).sample(texts3, min(args.cost_sample, len(texts3)))
    print("| threads | ms per window (window 3, batch 32) | whole store |")
    print("|---|---|---|")
    best = None
    for th in [int(x) for x in args.threads.split(",")]:
        emb = semsearch.Embedder(args.model, threads=th)
        embed_all(emb, sample[:64])
        t = time.time()
        embed_all(emb, sample)
        ms = (time.time() - t) / len(sample) * 1000
        best = min(best or (ms, th), (ms, th))
        print(f"| {th} | {ms:.1f} | {ms * len(rows) / 60000:.1f} min |")
    th = best[1]
    print(f"\nfastest: {th} threads. Query: ", end="")
    emb = semsearch.Embedder(args.model, threads=th)
    t = time.time()
    for _ in range(20):
        emb.embed(["방송에서 고양이 얘기한 부분"], semsearch.QUERY_PREFIX)
    print(f"{(time.time() - t) / 20 * 1000:.1f} ms per query embedding\n")
    print("| dim | vector table (float32) | matrix product over the store |")
    print("|---|---|---|")
    for dim in (768, 256, 128):
        m = np.random.default_rng(0).standard_normal((len(rows), dim)).astype(np.float32)
        q = m[0]
        t = time.time()
        for _ in range(20):
            m @ q
        took = (time.time() - t) / 20 * 1000
        print(f"| {dim} | {len(rows) * dim * 4 / 1e6:.1f} MB | {took:.2f} ms |")

    # [2] [3] quality
    qs = queries(rows, args.queries)
    pairs = {}
    for _, _, s, q in qs:
        pairs[f"{q}->{s}"] = pairs.get(f"{q}->{s}", 0) + 1
    print(f"\nqueries: {len(qs)} ({pairs}), top 10 over {len(rows)} cues\n")
    qtexts = [q for _, q, *_ in qs]
    qfull = np.concatenate([emb.embed(qtexts[i:i + 32], semsearch.QUERY_PREFIX, dim=768)
                            for i in range(0, len(qtexts), 32)])
    print("| window | dim | hit@1 (the cue) | hit@10 (the cue) | hit@10 (its passage) "
          "| MRR@10 (passage) |")
    print("|---|---|---|---|---|---|")
    for size in [int(x) for x in args.windows.split(",")]:
        t = time.time()
        full = embed_all(emb, [w[2] for w in semsearch.windows(rows, size)])
        took = time.time() - t
        for dim in (768, 256, 128):
            h1, h10, c10, mrr = evaluate(rows, mat=semsearch._cut(full, dim),
                                         qmat=semsearch._cut(qfull, dim), qs=qs, size=size)
            print(f"| {size} | {dim} | {h1:.0%} | {h10:.0%} | {c10:.0%} | {mrr:.3f} |")
        print(f"<!-- window {size}: indexed in {took / 60:.1f} min -->")


if __name__ == "__main__":
    main()
