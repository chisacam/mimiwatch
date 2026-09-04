"""Gather the results of running each context-line condition four times.

temperature is 0.2, so one result is only one sample. Whether `って。` coming
back untranslated at 5 lines was chance or a tendency can only be told by
repeating.
"""
import json, os, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
NS = [0, 1, 2, 3, 5, 8]
TAGS = ["", "-r1", "-r2", "-r3"]

def load(n, tag):
    p = os.path.join(HERE, f"out_prompt_ctx{n}{tag}.json")
    return json.load(open(p, encoding="utf-8"))["rows"]

# Hiragana or katakana left in a Korean translation means it was handed back
# rather than translated.
def leaked(r):
    return (not r["error"] and r["src_lang"] == "ja"
            and any(0x3040 <= ord(c) <= 0x30ff for c in r["out"]))

print("| context lines | source leaks (4 runs) | leaked lines | failures | median (s) |")
print("|---|---|---|---|---|")
detail = {}
for n in NS:
    tot, fails, secs, lines = 0, 0, [], {}
    for tag in TAGS:
        rows = load(n, tag)
        for r in rows:
            if leaked(r):
                tot += 1
                lines.setdefault(r["text"], []).append(r["out"])
            if r["error"]:
                fails += 1
        secs += [r["seconds"] for r in rows]
    detail[n] = lines
    names = ", ".join(f"`{t}`" for t in lines) or "-"
    print(f"| {n} | {tot}/{len(TAGS) * len(rows)} | {names} | {fails} | "
          f"{statistics.median(secs):.2f} |")

print("\nleaked content")
for n in NS:
    for text, outs in detail[n].items():
        print(f"  ctx{n}  {text!r} -> {outs}")
