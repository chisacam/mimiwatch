"""Check the rules that hold when a subtitle is edited by hand.

Transcription gets things wrong. It hears noise as speech, writes proper nouns
as something else, and the translation goes wrong once more on top of that. The
editing path has a few places where things break quietly, and those are what is
looked at here.

  - Editing the source text leaves the attached translation as **the old
    sentence's**. It must not be erased, only marked, and correcting the
    translation by hand must take that mark back down.
  - A bulk re-translation must not overwrite a translation a person edited. The
    mark left in `edited` is the ground for that decision.
  - Moving only the start time leaves the end time behind, producing a subtitle
    whose **end precedes its start**. SRT tools drop such a line, or refuse the
    whole file.

    .venv/bin/python bench/edit_check.py

It does not touch the real store -- it writes only in a temporary copy.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

FAIL = []


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def main():
    tmp = tempfile.mkdtemp(prefix="mw-edit-")
    try:
        os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
        import store
        store.DATA = os.path.join(tmp, "data")
        store.DB = os.path.join(store.DATA, "mimiwatch.db")
        store._db = None
        store.init()
        import export

        OWNER = "vid-1"
        store.save_doc(OWNER, {"id": OWNER, "title": "시험", "url": "",
                               "source_lang": "ja", "viewer_lang": "ko"})
        store.replace_cues(OWNER, [
            {"start": 10.0, "end": 12.0, "text": "いち", "lang": "ja",
             "translations": {"g": "하나"}},
            {"start": 20.0, "end": 22.5, "text": "に", "lang": "ja",
             "translations": {"g": "둘", "m": "둘(다른 엔진)"}},
            {"start": 30.0, "end": 40.0, "text": "さん", "lang": "ja",
             "translations": {}},
        ])
        check(len(store.cues(OWNER)) == 3, "3 test cue lines")

        print("\n[1] editing the source text")
        got = store.edit_cue(OWNER, 1, text="고친 원문")
        check(got["text"] == "고친 원문", "the source text changes")
        check("text" in got["edited"], f"the mismatch mark is attached (edited={got['edited']!r})")
        check(got["translations"].get("g") == "하나", "the translation is not erased")

        print("\n[2] correcting the translation by hand")
        got = store.edit_cue(OWNER, 1, tr="맞춘 번역", backend="g")
        check(got["translations"]["g"] == "맞춘 번역", "the translation changes")
        check("text" not in got["edited"], "the mismatch mark comes back down")
        check("tr" in got["edited"], f"the hand-edited mark stays ({got['edited']!r})")

        print("\n[3] another engine's translation is left alone")
        got = store.edit_cue(OWNER, 2, tr="새 번역", backend="g")
        check(got["translations"]["g"] == "새 번역", "only the chosen engine changes")
        check(got["translations"].get("m") == "둘(다른 엔진)", "the other engine's translation stays")

        print("\n[4] moving the start time carries the length along")
        got = store.edit_cue(OWNER, 3, start=5.0)
        check(abs(got["t"] - 5.0) < 1e-6, f"the start moves ({got['t']})")
        check(abs((got["end"] - got["t"]) - 10.0) < 1e-6,
              f"the 10 s length is kept ({got['end'] - got['t']:.2f})")
        check(got["end"] > got["t"], "the end is after the start")

        print("\n[5] pulling it earlier does not reverse it")
        store.update_cue(OWNER, 2, start=100.0, end=101.0)
        got = store.edit_cue(OWNER, 2, start=1.0)
        check(got["end"] > got["t"], f"end({got['end']:.2f}) > start({got['t']:.2f})")

        print("\n[6] export repairs a reversed span")
        # Corrupt the store directly, to see that a path not going through edit_cue is blocked too.
        store.update_cue(OWNER, 1, start=50.0, end=2.0)
        meta, rows = export.collect(OWNER)
        bad = [r for r in rows if r["end"] <= r["start"]]
        check(not bad, f"no reversed lines ({len(bad)})")
        body = export.render(meta, rows, "srt", "both")[0].decode()
        import re
        def secs(t):
            h, m, rest = t.split(":"); s2, ms = rest.split(",")
            return int(h) * 3600 + int(m) * 60 + int(s2) + int(ms) / 1000
        pairs = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", body)
        check(all(secs(b) > secs(a) for a, b in pairs),
              f"every SRT span runs forward ({len(pairs)} lines)")

        print("\n[7] deleting a line")
        n0 = store.cue_count(OWNER)
        check(store.delete_cue(OWNER, 2), "it is deleted")
        ids = [c["id"] for c in store.cues(OWNER)]
        check(store.cue_count(OWNER) == n0 - 1, f"one line fewer ({n0} -> {n0-1})")
        check(2 not in ids, "that line is gone")
        check(1 in ids and 3 in ids, f"the remaining ids are not renumbered ({ids})")
        # The remaining lines are ordered **by time**, not by id. This spot used
        # to check `ids == sorted(ids)` as well, but [6] just above pushes line
        # 1's start out to 50 s, so that demand runs against what store.cues()
        # promises -- a line a person wrote in (insert_cue) always gets the last
        # id while its time is somewhere in a gap, so handing them back in id
        # order throws off both the export order and the translation context
        # (store.cues docstring).
        ts = [c["t"] for c in store.cues(OWNER)]
        check(ts == sorted(ts), f"the remaining lines are in time order ({ts})")
        check(not store.delete_cue(OWNER, 2), "a line that is already gone gives False")

        print("\n[8] a machine translation takes the mismatch mark down")
        store.replace_cues(OWNER + "-b", [
            {"start": 0.0, "end": 1.0, "text": "もと", "lang": "ja",
             "translations": {"g": "옛 번역"}},
        ])
        o2 = OWNER + "-b"
        store.edit_cue(o2, 1, text="고친 원문")
        check("text" in store.cues(o2)[0]["edited"], "editing the source text raises the mismatch mark")
        store.save_translation(o2, 1, "g", "새 번역")
        got = store.cues(o2)[0]
        check("text" not in got["edited"],
              f"the mark comes down after a machine translation ({got['edited']!r})")
        check(got["translations"]["g"] == "새 번역", "the translation was replaced")
        # A machine translation does not erase the mark that a person edited it.
        store.edit_cue(o2, 1, tr="사람 번역", backend="g")
        store.save_translation(o2, 1, "g", "기계가 또 씀")
        check("tr" in store.cues(o2)[0]["edited"],
              "a machine translation does not erase the hand-edited mark")

        print("\n[9] things that are not there")
        check(store.edit_cue(OWNER, 999, text="x") is None, "a missing line gives None")
        check(store.edit_cue("no-such-owner", 1, text="x") is None,
              "a missing owner gives None too")
        check(store.owner_of("live:abc") == "abc", "the live: prefix is stripped")
        check(store.owner_of("abc") == "abc", "a video id passes through")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("all passed" if not FAIL else f"{len(FAIL)} failed: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
