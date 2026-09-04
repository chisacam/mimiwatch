"""Check that unifying storage on SQLite moved the data across untouched.

A VOD used to keep its metadata and its subtitles together in a single
`data/<video id>.json` file, while live sat in SQLite. With two storage shapes
there were two read/write paths, and export, editing and re-translation each
had to carry one more fork.

This test looks at **whether the migrating code loses a single character**. It
builds files here in the shape the old version left behind, migrates them into
a temporary store, and compares the result against what it built.

There is a reason it does not compare against the real store. After the
migration people go on editing, deleting and re-transcribing subtitles -- the
real store diverging from the originals is the normal outcome, and recording
that as a failure means ordinary work breaks the test. It rang that way once.

The sample is built here for the same reason. Leaning on `data/legacy/` means
that the moment that folder is cleared away (it is a folder you may clear) the
test looks at nothing at all.

    .venv/bin/python bench/store_migrate.py

Writes nothing to the store -- it only reads, and the write round trip happens
on a temporary copy.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import store                                                 # noqa: E402

FAIL = []


def check(cond, what):
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAIL.append(what)


def norm(cue: dict) -> dict:
    """An empty field is the same whether it is there or not. The old files did
    not write speaker at all, and the table leaves it as an empty string."""
    return {k: v for k, v in cue.items() if v not in ("", {}, None)}


def main():
    print("\n[1] schema")
    import sqlite3
    db = sqlite3.connect(f"file:{store.DB}?mode=ro", uri=True)
    cols = [r[1] for r in db.execute("PRAGMA table_info(cues)")]
    for c in ("owner", "start", "end"):
        check(c in cols, f"cues.{c} exists")
    for gone in ("session", "t"):
        check(gone not in cols, f"the old name cues.{gone} is gone")
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check("docs" in tables, "the docs table exists")
    idx = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    check("cues_owner_start" in idx, "the owner+start index exists")

    print("\n[2] does the migrating code move the originals across untouched")
    # Built exactly in the shape the old version left behind. The last one is
    # an even older version that hung a single translation on each subtitle --
    # this checks that folding too.
    FIXTURES = {
        "vid-new": {
            "id": "vid-new", "title": "새 판 · 한글과 日本語",
            "url": "https://youtu.be/vid-new", "duration": 90.0,
            "uploader": "누군가", "is_live": False, "source_lang": "ja",
            "viewer_lang": "ko", "translated": True, "audio_seconds": 90.0,
            "lang_counts": {"ja": 2},
            "cues": [
                {"start": 1.5, "end": 3.25, "lang": "ja", "text": "いち",
                 "translations": {"g": "하나", "m": "하나(다른 엔진)"}},
                {"start": 10.0, "end": 12.0, "lang": "ja", "text": "に",
                 "translations": {}, "speaker": "A"},
            ],
        },
        "vid-old": {
            "id": "vid-old", "title": "옛 판", "url": "", "duration": None,
            "uploader": "", "is_live": False, "source_lang": "en",
            "viewer_lang": "ko", "translated": True, "audio_seconds": 5.0,
            "lang_counts": {"en": 1},
            "cues": [
                {"start": 0.0, "end": 2.0, "lang": "en", "text": "one",
                 "translation": "하나"},
                {"start": 2.0, "end": 4.0, "lang": "en", "text": "two",
                 "translation": ""},
            ],
        },
    }
    tmp = tempfile.mkdtemp(prefix="mw-mig-")
    try:
        os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
        for vid, doc in FIXTURES.items():
            with open(os.path.join(tmp, "data", vid + ".json"), "w",
                      encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
        real = (store.DATA, store.DB, store.LEGACY, store._db)
        store.DATA = os.path.join(tmp, "data")
        store.DB = os.path.join(store.DATA, "mimiwatch.db")
        store.LEGACY = os.path.join(store.DATA, "legacy")
        store._db = None
        store.init()
        moved = store.import_legacy_docs()
        check(moved == len(FIXTURES), f"{len(FIXTURES)} moved across ({moved})")
        for vid, doc in FIXTURES.items():
            old = json.loads(json.dumps(doc))      # leave the original alone
            old_cues = old.pop("cues")
            for c in old_cues:
                if "translations" not in c:
                    c["translations"] = ({"local-m2m100": c["translation"]}
                                         if c.get("translation") else {})
                c.pop("translation", None)
            meta = store.doc(vid)
            new_cues = [{"start": c["t"], "end": c["end"], "lang": c["lang"],
                         "text": c["text"], "translations": c["translations"]}
                        | ({"speaker": c["speaker"]} if c["speaker"] else {})
                        for c in store.cues(vid)]
            same_n = len(old_cues) == len(new_cues)
            same_c = same_n and all(norm(a) == norm(b)
                                    for a, b in zip(old_cues, new_cues))
            old.pop("backends_done", None)
            keys = {k for k in set(old) | set(meta or {}) if k != "backends_done"}
            same_m = meta is not None and all(old.get(k) == meta.get(k) for k in keys)
            check(same_n and same_c and same_m,
                  f"{vid}: {len(old_cues)} cue lines and {len(keys)} meta fields unchanged"
                  + ("" if same_n else f" (line count {len(old_cues)}->{len(new_cues)})")
                  + ("" if same_c or not same_n else " (content differs)")
                  + ("" if same_m else " (meta differs)"))
        # Folding the old version's translation
        got = store.cues("vid-old")
        check(got[0]["translations"] == {"local-m2m100": "하나"},
              f"the old translation folds into a backend map ({got[0]['translations']})")
        check(got[1]["translations"] == {},
              "an empty translation becomes an empty map")
        check(store.doc("vid-old")["backends_done"] == ["local-m2m100"],
              "backends_done is recounted from the cues")
        check(not [n for n in os.listdir(os.path.join(tmp, "data"))
                   if n.endswith(".json")], "the migrated originals do not stay in data/")
        check(len(os.listdir(store.LEGACY)) == len(FIXTURES),
              "the originals move into legacy/ (nothing is deleted)")
        check(store.import_legacy_docs() == 0, "a second run has nothing left to move")
    finally:
        store.DATA, store.DB, store.LEGACY, store._db = real
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[3] are the live cues unchanged")
    sess = [s for s in store.sessions(200) if s.get("cues")]
    check(len(sess) > 0, f"{len(sess)} sessions with cues")
    for s in sess[:3]:
        cs = store.cues(s["id"])
        check(len(cs) == s["cues"], f"{s['id']}: the counted number matches the number read "
                                    f"({s['cues']} / {len(cs)})")
        check(all("t" in c for c in cs),
              f"{s['id']}: the start-time key comes back as t (the wire format is kept)")

    print("\n[4] editing a single cue line (on a copy)")
    tmp = tempfile.mkdtemp(prefix="mw-store-")
    try:
        # The WAL travels along too. Copying only the main file drops
        # everything not yet checkpointed, and you end up looking at a DB
        # without the VOD you just put in.
        for suffix in ("", "-wal", "-shm"):
            src = store.DB + suffix
            if os.path.exists(src):
                shutil.copy(src, os.path.join(tmp, "mimiwatch.db" + suffix))
        code = (
            "import sys, json; sys.path.insert(0, %r);\n"
            "import store;\n"
            "store.DATA = %r; store.DB = %r; store._db = None;\n"
            "ids = store.doc_ids();\n"
            "vid = ids[0];\n"
            "before = store.cues(vid);\n"
            "ok = store.update_cue(vid, before[1]['id'], text='바꾼 줄');\n"
            "after = store.cues(vid);\n"
            "print(json.dumps({'ok': ok, 'n0': len(before), 'n1': len(after),\n"
            "  'changed': after[1]['text'], 'neighbour_same':\n"
            "  before[0]['text'] == after[0]['text'] and\n"
            "  before[2]['text'] == after[2]['text'],\n"
            "  'tr_kept': before[1]['translations'] == after[1]['translations']}))"
        ) % (HERE, tmp, os.path.join(tmp, "mimiwatch.db"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, cwd=tmp)
        line = [x for x in out.stdout.splitlines() if x.startswith("{")]
        if not line:
            check(False, f"could not run on the copy: {out.stderr[-300:]}")
        else:
            r = json.loads(line[-1])
            check(r["ok"], "update_cue changed one line")
            check(r["n0"] == r["n1"], f"the line count is unchanged ({r['n0']})")
            check(r["changed"] == "바꾼 줄", "the edited line came through")
            check(r["neighbour_same"], "the lines either side were left alone")
            check(r["tr_kept"], "that line's translation is still there")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("all passed" if not FAIL else f"{len(FAIL)} failed: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
