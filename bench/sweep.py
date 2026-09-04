"""Vary the number of context lines and look at what changes.

There is no automatic way to score quality, so three proxy metrics are used.

  failure        Lines whose translation was thrown away by `looks_broken`. It
                 rises as the contamination gets worse.
  length blow-up Output more than three times the source. This is the shape it
                 takes when the model has translated the reference lines too.
  source leak    Lines where Japanese kana remain in the output. Given deep
                 context the model hands the source back instead of translating
                 it (`って。` -> `って`). This is the worst failure --
                 `looks_broken` cannot catch it. It is not empty, not long, and
                 has no `⁇`.
  change         Lines that differ character for character from ctx0 (no
                 context). This is how much the context actually did.

A metric does not stand in for a judgement. Which lines changed is for a person to see.
"""
import json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
NS = [0, 1, 2, 3, 5, 8]
load = lambda n: json.load(open(os.path.join(HERE, f"out_prompt_ctx{n}.json"),
                                encoding="utf-8"))["rows"]
runs = {n: load(n) for n in NS}
base = runs[0]

# Hiragana and katakana. Kanji are not counted -- kanji left in a Korean
# translation is rare but can be legitimate, while kana is not.
KANA = [(0x3040, 0x30ff)]


def leaks(row):
    if row["error"] or row["src_lang"] != "ja":
        return False
    return any(lo <= ord(ch) <= hi for ch in row["out"] for lo, hi in KANA)


print("| 문맥 줄 | 실패 | 길이폭발 | 원문유출 | ctx0 대비 변화 | 중앙값(초) |")
print("|---|---|---|---|---|---|")
for n in NS:
    rows = runs[n]
    fails = sum(1 for r in rows if r["error"])
    blown = sum(1 for r in rows if not r["error"] and len(r["text"]) >= 4
                and len(r["out"]) > len(r["text"]) * 3)
    leaked = [r for r in rows if leaks(r)]
    diff = sum(1 for a, b in zip(base, rows) if a["out"] != b["out"])
    secs = sorted(r["seconds"] for r in rows)
    print(f"| {n} | {fails} | {blown} | {len(leaked)} | {diff} | "
          f"{statistics.median(secs):.2f} |")
    for r in leaked:
        print(f"|   ↳ | | | `{r['text']}` → `{r['out']}` | | |")

if len(sys.argv) > 1:                    # how one particular line changes across the runs
    key = sys.argv[1]
    idx = [i for i, r in enumerate(base) if r["text"].startswith(key)]
    for i in idx:
        print(f"\n원문: {base[i]['text'][:60]}")
        print(f"  직전: {' / '.join((base[i].get('context') or [])[-8:])[:100]}")
        for n in NS:
            print(f"  ctx{n}: {runs[n][i]['out'] or runs[n][i]['error']}")
