"""네 가지 프롬프트 조합을 한 표에 놓습니다."""
from __future__ import annotations
import json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODES = ["generic", "preset", "generic-ctx", "preset-ctx"]
load = lambda m: json.load(open(os.path.join(HERE, f"out_prompt_{m}.json"), encoding="utf-8"))
runs = {m: load(m) for m in MODES}

secs = {m: sorted(r["seconds"] for r in runs[m]["rows"]) for m in MODES}
print("| | " + " | ".join(MODES) + " |")
print("|---|" + "---|" * len(MODES))
print("| 중앙값(초) | " + " | ".join(f"{statistics.median(secs[m]):.2f}" for m in MODES) + " |")
print("| 합계(초) | " + " | ".join(f"{sum(secs[m]):.1f}" for m in MODES) + " |")
print()

only = sys.argv[1] if len(sys.argv) > 1 else None
rows = list(zip(*[runs[m]["rows"] for m in MODES]))
print("| 원문 | " + " | ".join(MODES) + " |")
print("|---|" + "---|" * len(MODES))
esc = lambda s: (s or "").replace("|", "\\|").replace("\n", " ")
for tup in rows:
    if only and tup[0]["genre" if only != "general" else "genre"] and only not in (tup[1]["genre"],):
        continue
    outs = [esc(r["out"] or r["error"]) for r in tup]
    if len(set(outs)) == 1:
        continue                      # 넷이 똑같으면 볼 것이 없습니다
    print(f"| {esc(tup[0]['text'])} | " + " | ".join(outs) + " |")
