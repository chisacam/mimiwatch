"""How much does each model add to a line that gives it nothing to go on?

Reading the table by eye, TranslateGemma's misses cluster on short lines --
it finishes the thought the speaker had not finished. This counts that
instead of asserting it: the output/source character ratio, split at the
six-character mark RESULTS.md already uses to define a fragment.
"""
import json, os, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
load = lambda n: json.load(open(os.path.join(HERE, f"out_{n}.json"), encoding="utf-8"))

a, b = load("gemma4"), load("translategemma")
for label, lo, hi in [("6자 이하 (파편)", 0, 6), ("7~20자", 7, 20), ("21자 이상", 21, 10**6)]:
    ra = [len(r["out"]) / len(r["text"]) for r in a["rows"] if lo <= len(r["text"]) <= hi]
    rb = [len(r["out"]) / len(r["text"]) for r in b["rows"] if lo <= len(r["text"]) <= hi]
    print(f"{label:<16} n={len(ra):>2}  Gemma 4 {statistics.median(ra):.2f}x   "
          f"TranslateGemma {statistics.median(rb):.2f}x")
