"""What one document pass costs, over a real transcript.

The document view (outline.py) runs a whole generation every minute or two on
the engine that is also translating the subtitles, and the cadence in live.py
was chosen before any of it had been measured. Nothing in RESULTS had a number
to choose it from either: every Gemma measurement in this repository is over
one subtitle line with at most three lines of context (sections 20, 28, 29), and
this prompt is two orders of magnitude bigger.

Two things are being asked.

  [1] Does the prompt fit? `n_ctx` is 2048 and it is part of the key
      models.shared() files Gemma under, so the document cannot have a roomier
      context without a second copy of the weights in memory. outline.py sizes
      its window in characters against that, counting a character as a token --
      right for Korean and Japanese, wrong for English by three or four times.
      If that estimate is too generous the speech is truncated silently, which
      is the failure this has to rule out.

  [2] What does a pass cost, and how much speech does one hold? Together those
      decide the cadence: a pass that costs five seconds and covers three
      minutes of a talk is a different setting from one that costs thirty.

It loads a model and reads the store, so it is not a `*_check.py` and
check_all.py does not run it.

    .venv/bin/python bench/outline_cost.py <owner> [--passes N]

`<owner>` is a session id or a video id -- whatever `store.cues` is filed
under. `--passes` stops early; the default walks the whole transcript.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import outline as mw_outline     # noqa: E402
import store                     # noqa: E402
import translate as mw_translate  # noqa: E402


def speech(owner: str) -> list[dict]:
    """The cues a pass would be given. The same filter live.py uses -- a note
    is the server talking, not the speaker."""
    return [c for c in store.cues(owner)
            if c.get("kind") != "note" and (c.get("text") or "").strip()]


def windows(cues: list[dict], budget: int):
    """Split into windows the way live._ol_take does: fill to the budget and
    never split a line."""
    out, cur, used = [], [], 0
    for c in cues:
        text = c["text"].strip()
        if cur and used + len(text) + 1 > budget:
            out.append(cur)
            cur, used = [], 0
        cur.append(c)
        used += len(text) + 1
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("owner")
    ap.add_argument("--passes", type=int, default=0)
    ap.add_argument("--lang", default="ko")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    store.init()
    cues = speech(args.owner)
    if not cues:
        print(f"no subtitles under {args.owner!r}", file=sys.stderr)
        return 1

    # Built straight from a spec rather than from the configured engine: the
    # point is to measure Gemma, and the machine this runs on may well have the
    # light default selected.
    writer = mw_translate.build_writer({"backend": "gemma", "device": args.device})
    budget = mw_outline.window_chars(writer)
    blocks = windows(cues, budget)
    if args.passes:
        blocks = blocks[:args.passes]

    total_s = sum(c.get("t", 0.0) for c in cues[-1:]) - cues[0].get("t", 0.0)
    print(f"owner        {args.owner}")
    print(f"speech       {len(cues)} lines · "
          f"{sum(len(c['text']) for c in cues)} chars · {total_s / 60:.0f} min")
    print(f"window       {budget} chars (n_ctx {writer.context_tokens})")
    print(f"passes       {len(blocks)}")
    print()

    doc = mw_outline.empty()
    took = []
    for i, block in enumerate(blocks, 1):
        window = "\n".join(c["text"].strip() for c in block)
        at = block[0].get("t", 0.0)
        prompt = mw_outline.build_prompt(doc, window, args.lang)
        t0 = time.time()
        try:
            doc = mw_outline.advance(doc, window, args.lang, writer, at=at,
                                     lines=len(block))
        except Exception as exc:
            print(f"  pass {i}: FAILED {exc}")
            break
        dt = time.time() - t0
        took.append(dt)
        covered = (block[-1].get("t", 0.0) - at) / 60
        print(f"  pass {i:3d}  {dt:6.1f}s  prompt {len(prompt):5d} chars  "
              f"{len(block):3d} lines  {covered:4.1f} min of talk  "
              f"-> {len(doc['sections'])} sections")

    if took:
        ordered = sorted(took)
        print()
        print(f"per pass     median {ordered[len(ordered) // 2]:.1f}s · "
              f"min {ordered[0]:.1f}s · max {ordered[-1]:.1f}s")
        print(f"total        {sum(took):.0f}s for {len(took)} passes")
    print()
    print(mw_outline.to_markdown(doc, args.owner))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
