"""Check whether the context pushed the target line out.

The contamination shows up in two shapes. Either the translation is filled with
the content of the reference lines (`ここまで。` -> "이번에 V스포 보컬 노래
방송으로요."), or it falls over into an outright failure. Both make the length
jump sharply against the source, so that ratio finds the candidates and a person
confirms them.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
load = lambda m: json.load(open(os.path.join(HERE, f"out_prompt_{m}.json"), encoding="utf-8"))["rows"]

for mode in ["generic", "preset", "generic-ctx", "preset-ctx"]:
    rows = load(mode)
    fails = [r for r in rows if r["error"]]
    # Output more than three times the source. There is no reason for the
    # translation of one subtitle line to grow this long -- a floor keeps out
    # the cases where the source is very short.
    blown = [r for r in rows
             if not r["error"] and len(r["text"]) >= 4
             and len(r["out"]) > len(r["text"]) * 3]
    print(f"{mode:<12} 실패 {len(fails)}  길이폭발 {len(blown)}")
    for r in fails + blown:
        print(f"    {r['text'][:24]!r} -> {(r['out'] or r['error'])[:60]!r}")
