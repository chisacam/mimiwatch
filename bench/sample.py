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


# 표본에 담아 둘 직전 자막의 개수. 실제로 몇 줄을 넘길지는 여기서 정하지
# 않습니다 -- `run_prompt.py`가 이 목록을 뒤에서부터 잘라 씁니다. 몇 줄이
# 최적인지가 측정 대상이므로, 표본은 넉넉히 담아 두고 자르는 쪽을 바꿉니다.
CONTEXT_LINES = 8


def pick(name, lang, n):
    """Spread the picks across the whole recording, short lines included.

    각 줄에 직전 자막도 함께 담습니다. 표본은 시간축을 건너뛰며 뽑지만
    문맥은 원본에서 실제로 그 줄 앞에 있던 것이어야 합니다.
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
    # 5절에서 옮겨 온 기준점에는 앞 줄이 없습니다. 문맥 없는 경우도
    # 실제로 생기므로(세션 첫 줄) 그대로 둡니다.
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
