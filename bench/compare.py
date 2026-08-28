"""Put the two runs side by side.

Latency is reported as a median rather than a mean because one 9-second
outlier -- a model that decided to explain itself -- would otherwise hide
sixty fast lines.
"""
from __future__ import annotations

import json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(HERE, f"out_{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def summarise(run):
    ok = [r for r in run["rows"] if r["out"] and not r["error"]]
    secs = sorted(r["seconds"] for r in run["rows"])
    return {
        "model": run["model"], "file": run["file"],
        "load": run["load_seconds"],
        "n": len(run["rows"]), "answered": len(ok),
        "failed": len(run["rows"]) - len(ok),
        "median": round(statistics.median(secs), 2),
        "p90": round(secs[int(len(secs) * 0.9)], 2),
        "max": round(secs[-1], 2),
        "total": round(sum(secs), 1),
    }


def main():
    a, b = load("gemma4"), load("translategemma")
    sa, sb = summarise(a), summarise(b)
    print("| | Gemma 4 E4B q4_0 | TranslateGemma 4B Q4_K_M |")
    print("|---|---|---|")
    for k, label in [("file", "파일"), ("load", "적재(초)"),
                     ("answered", "응답"), ("failed", "실패"),
                     ("median", "중앙값(초)"), ("p90", "p90(초)"),
                     ("max", "최대(초)"), ("total", "합계(초)")]:
        print(f"| {label} | {sa[k]} | {sb[k]} |")
    print()
    print("| 원문 | Gemma 4 | TranslateGemma | 초 (G4 / TG) |")
    print("|---|---|---|---|")
    for ra, rb in zip(a["rows"], b["rows"]):
        assert ra["text"] == rb["text"]
        oa = ra["out"] or f"**{ra['error']}**"
        ob = rb["out"] or f"**{rb['error']}**"
        esc = lambda s: s.replace("|", "\\|").replace("\n", " ")
        print(f"| {esc(ra['text'])} | {esc(oa)} | {esc(ob)} | "
              f"{ra['seconds']:.2f} / {rb['seconds']:.2f} |")


if __name__ == "__main__":
    main()
