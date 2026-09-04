"""Check that exported subtitles keep to their format.

A live cue has no end time. What knows the moment an utterance ends is the VAD,
and the cue is finalised later than that carrying only a start time. SRT/VTT
demand an end time, so export.py invents one, and when that rule goes wrong a
tool either drops the subtitles wholesale or leaves one line on screen for
minutes at a time.

    .venv/bin/python bench/export_check.py

It does not touch the store -- it only reads.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import export                                                # noqa: E402
import store                                                 # noqa: E402

FAIL = []


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def secs(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = re.split("[,.]", rest)
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def fake_rows():
    """Lines picked to hit exactly the places where the rules bite."""
    return [
        {"t": 0.0, "text": "첫 줄", "kind": "final", "translations": {"b": "first"}},
        # The next line 0.3 s later: the minimum length bites
        {"t": 0.3, "text": "바로 다음", "kind": "final", "translations": {"b": "next"}},
        # 60 s of silence: the maximum length bites
        {"t": 60.3, "text": "한참 뒤", "kind": "final", "translations": {}},
        {"t": 61.0, "kind": "note", "text": "⋯ 못 받았습니다 ⋯", "translations": {}},
        {"t": 62.0, "text": "마지막", "kind": "final", "translations": {"b": "last"}},
    ]


def main():
    print("\n[1] end-time rules (live)")
    rows = []
    cues = fake_rows()
    for i, c in enumerate(cues):
        start = c["t"]
        nxt = cues[i + 1]["t"] if i + 1 < len(cues) else None
        span = (nxt - start) if nxt is not None else export.END_MAX_S
        span = max(export.END_MIN_S, min(export.END_MAX_S, span))
        rows.append({"start": start, "end": start + span, "text": c["text"],
                     "tr": next(iter(c["translations"].values()), ""),
                     "speaker": "", "kind": c["kind"]})
    d = [r["end"] - r["start"] for r in rows]
    check(abs(d[0] - export.END_MIN_S) < 1e-6,
          f"a tightly following line gets the {export.END_MIN_S} s minimum ({d[0]:.2f})")
    # The 60 s jump is between the second line (0.3 s) and the third (60.3 s).
    check(abs(d[1] - export.END_MAX_S) < 1e-6,
          f"a long gap gets the {export.END_MAX_S} s maximum ({d[1]:.2f})")
    # A note follows 0.7 s after the third line, so the minimum length bites.
    check(abs(d[2] - export.END_MIN_S) < 1e-6,
          f"a note right behind still gets the minimum length ({d[2]:.2f})")
    check(abs(d[4] - export.END_MAX_S) < 1e-6, f"the last line gets the cap too ({d[4]:.2f})")
    check(all(x > 0 for x in d), "every length is positive")

    meta = {"title": "시험 / 제목: 살아있나?", "url": "", "value": "x",
            "source_lang": "ja", "viewer_lang": "ko", "live": True}

    print("\n[2] SRT")
    body, mime = export.render(meta, rows, "srt", "both")
    txt = body.decode()
    check("subrip" in mime, f"MIME ({mime})")
    blocks = [b for b in txt.strip().split("\n\n") if b.strip()]
    check(len(blocks) == 4, f"the note drops out, leaving 4 blocks ({len(blocks)})")
    check("못 받았습니다" not in txt, "the note is not in the subtitle track")
    nums = [int(b.split("\n")[0]) for b in blocks]
    check(nums == [1, 2, 3, 4], f"the numbering runs on from 1 ({nums})")
    arrows = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", txt)
    check(len(arrows) == 4, f"{len(arrows)} timing lines")
    check(all(secs(b) > secs(a) for a, b in arrows), "the end is always after the start")
    check(all(secs(arrows[i][0]) <= secs(arrows[i + 1][0]) for i in range(3)),
          "start times ascend")
    check(txt.startswith("1\n00:00:00,000 --> 00:00:00,800"),
          "first block: " + txt.split("\n\n")[0].replace("\n", " | "))
    check("첫 줄\nfirst" in txt, "the both view puts the translation after the source")

    print("\n[3] VTT")
    body, mime = export.render(meta, rows, "vtt", "both")
    txt = body.decode()
    check(txt.startswith("WEBVTT\n"), "starts with WEBVTT")
    check("text/vtt" in mime, f"MIME ({mime})")
    check(re.search(r"\d\d:\d\d:\d\d\.\d\d\d --> ", txt) is not None,
          "the millisecond separator is a dot")
    check("," not in re.findall(r"[\d:,.]+ --> [\d:,.]+", txt)[0],
          "no comma in the timings")

    print("\n[4] views (both / tr / src)")
    src = export.render(meta, rows, "srt", "src")[0].decode()
    tr = export.render(meta, rows, "srt", "tr")[0].decode()
    check("first" not in src and "첫 줄" in src, "source only: no translation")
    check("첫 줄" not in tr and "first" in tr, "translation only: no source")
    check("한참 뒤" in tr, "a line with no translation stays as its source in the translation-only view")

    print("\n[5] TXT / JSON")
    txt = export.render(meta, rows, "txt", "both")[0].decode()
    check(txt.startswith("# 시험 / 제목: 살아있나?"), "title header")
    check("[00:00] 첫 줄" in txt, "timestamps are attached")
    check("못 받았습니다" in txt, "the note stays in the readable transcript")
    js = export.render(meta, rows, "json", "both")[0].decode()
    import json as _j
    d = _j.loads(js)
    check(len(d["cues"]) == 5, f"json keeps the note too, {len(d['cues'])} lines")
    check(d["meta"]["title"] == meta["title"], "json carries the meta")

    print("\n[6] file name")
    n = export.filename(meta, "srt")
    check(not set(n) & set('\\/:*?"<>|'), f"no forbidden characters ({n})")
    check(n.endswith(".srt"), "extension")
    check(export.filename({"title": ""}, "vtt") == "mimiwatch.vtt", "an empty title falls back to the default")
    long = export.filename({"title": "가" * 300}, "txt")
    check(len(long) <= 84, f"not too long ({len(long)} chars)")

    print("\n[7] against real subtitles")
    sid = None
    for row in store.sessions(50):
        if row.get("cues"):
            sid = row["id"]; break
    if not sid:
        print("  ----  no stored live session, skipping")
    else:
        meta2, rows2 = export.collect("live:" + sid)
        check(len(rows2) > 0, f"{len(rows2)} lines from {meta2['title'][:24]!r}")
        out = export.render(meta2, rows2, "srt", "both")[0].decode()
        pairs = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", out)
        check(all(secs(b) > secs(a) for a, b in pairs), "the end is always after the start")
        over = sum(1 for i in range(len(pairs) - 1)
                   if secs(pairs[i][1]) > secs(pairs[i + 1][0]) + 1e-6)
        check(over <= len(pairs) * 0.2,
              f"overlapping subtitles are rare ({over}/{len(pairs)})")
        longest = max((secs(b) - secs(a)) for a, b in pairs)
        check(longest <= export.END_MAX_S + 1e-6,
              f"the longest subtitle is within the cap ({longest:.1f}s)")

    print("\n[8] things that are not there")
    for bad in ("live:nope", "not-a-video"):
        try:
            export.collect(bad); check(False, f"{bad} must be rejected")
        except KeyError:
            check(True, f"{bad} is rejected")
    try:
        export.render(meta, rows, "docx", "both"); check(False, "an unknown format must be rejected")
    except ValueError:
        check(True, "an unknown format is rejected")

    print("\n" + ("all passed" if not FAIL else f"{len(FAIL)} failed: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
