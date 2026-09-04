"""Pick the subtitle lines both translators will be judged on.

The sample has to look like what the app actually feeds a translator: a
live game stream is 38-55% fragments of six characters or fewer, so a
sample of tidy full sentences would flatter both models and tell us
nothing about the case that breaks them.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")

# The lines RESULTS.md already recorded M2M-100 failing on. They stay in
# every sample so the comparison keeps a fixed reference point.
ANCHORS = [
    ("ja", "社長とお料理企画"),
    ("ja", "まずホロメンいこうよ"),
    ("ja", "やろっかー。"),
]

SOURCES = [
    ("MPSTWzF2ZKU.json", "ja", 34),   # game stream: fragments, overlap, slang
    ("71SnC4H-G1Q.json", "ja", 14),   # singing stream: song titles, callouts
    ("jrLVa1Md4GU.json", "en", 12),   # conference talk: full sentences, jargon
]


def cues(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)["cues"]


# How many preceding subtitle lines the sample carries. How many actually get
# handed over is not decided here -- `run_prompt.py` trims this list from the
# end. How many lines is optimal is itself what is being measured, so the sample
# keeps plenty and the trimming side is what varies.
CONTEXT_LINES = 8


def pick(name, lang, n):
    """Spread the picks across the whole recording, short lines included.

    Each line carries the subtitles right before it as well. The sample is
    picked by skipping along the time axis, but the context has to be what
    actually stood in front of that line in the original.
    """
    rows = [c for c in cues(name) if (c.get("text") or "").strip()]
    if not rows:
        return []
    step = max(1, len(rows) // n)
    out, seen = [], set()
    for idx in range(0, len(rows), step):
        c = rows[idx]
        t = c["text"].strip()
        if t in seen:
            continue
        seen.add(t)
        prev = [r["text"].strip()
                for r in rows[max(0, idx - CONTEXT_LINES):idx]]
        out.append({"src_lang": lang, "text": t, "source": name,
                    "start": c.get("start"), "context": prev})
        if len(out) >= n:
            break
    return out


def build():
    # The anchors carried over from section 5 have no preceding lines. The
    # case of no context really does occur (the first line of a session), so
    # they are left as they are.
    items = [{"src_lang": l, "text": t, "source": "RESULTS.md", "start": None,
              "context": []}
             for l, t in ANCHORS]
    for name, lang, n in SOURCES:
        items += pick(name, lang, n)
    return items


if __name__ == "__main__":
    items = build()
    path = os.path.join(HERE, "sample.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print(f"{len(items)} lines -> {path}")
    for i in items[:8]:
        print(" ", i["src_lang"], i["text"][:50])
