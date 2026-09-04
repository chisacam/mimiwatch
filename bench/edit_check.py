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
        check(len(store.cues(OWNER)) == 3, "시험용 자막 3줄")

        print("\n[1] 원문을 고치면")
        got = store.edit_cue(OWNER, 1, text="고친 원문")
        check(got["text"] == "고친 원문", "원문이 바뀐다")
        check("text" in got["edited"], f"어긋남 표시가 붙는다 (edited={got['edited']!r})")
        check(got["translations"].get("g") == "하나", "번역은 지우지 않는다")

        print("\n[2] 번역을 손으로 맞추면")
        got = store.edit_cue(OWNER, 1, tr="맞춘 번역", backend="g")
        check(got["translations"]["g"] == "맞춘 번역", "번역이 바뀐다")
        check("text" not in got["edited"], "어긋남 표시가 내려간다")
        check("tr" in got["edited"], f"사람이 고쳤다는 표시는 남는다 ({got['edited']!r})")

        print("\n[3] 다른 엔진의 번역은 건드리지 않는다")
        got = store.edit_cue(OWNER, 2, tr="새 번역", backend="g")
        check(got["translations"]["g"] == "새 번역", "고른 엔진만 바뀐다")
        check(got["translations"].get("m") == "둘(다른 엔진)", "다른 엔진 번역이 남는다")

        print("\n[4] 시작 시각을 옮기면 길이가 따라간다")
        got = store.edit_cue(OWNER, 3, start=5.0)
        check(abs(got["t"] - 5.0) < 1e-6, f"시작이 옮겨진다 ({got['t']})")
        check(abs((got["end"] - got["t"]) - 10.0) < 1e-6,
              f"길이 10초가 유지된다 ({got['end'] - got['t']:.2f})")
        check(got["end"] > got["t"], "끝이 시작보다 뒤")

        print("\n[5] 앞으로 당겨도 거꾸로 되지 않는다")
        store.update_cue(OWNER, 2, start=100.0, end=101.0)
        got = store.edit_cue(OWNER, 2, start=1.0)
        check(got["end"] > got["t"], f"끝({got['end']:.2f}) > 시작({got['t']:.2f})")

        print("\n[6] 내보내기가 거꾸로 된 구간을 손본다")
        # Corrupt the store directly, to see that a path not going through edit_cue is blocked too.
        store.update_cue(OWNER, 1, start=50.0, end=2.0)
        meta, rows = export.collect(OWNER)
        bad = [r for r in rows if r["end"] <= r["start"]]
        check(not bad, f"거꾸로 된 줄이 없다 ({len(bad)}건)")
        body = export.render(meta, rows, "srt", "both")[0].decode()
        import re
        def secs(t):
            h, m, rest = t.split(":"); s2, ms = rest.split(",")
            return int(h) * 3600 + int(m) * 60 + int(s2) + int(ms) / 1000
        pairs = re.findall(r"(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)", body)
        check(all(secs(b) > secs(a) for a, b in pairs),
              f"SRT 의 모든 구간이 정방향 ({len(pairs)}줄)")

        print("\n[7] 줄 지우기")
        n0 = store.cue_count(OWNER)
        check(store.delete_cue(OWNER, 2), "지워진다")
        ids = [c["id"] for c in store.cues(OWNER)]
        check(store.cue_count(OWNER) == n0 - 1, f"한 줄 줄었다 ({n0} -> {n0-1})")
        check(2 not in ids, "그 줄이 없다")
        check(1 in ids and 3 in ids, f"남은 번호는 다시 매기지 않는다 ({ids})")
        # The remaining lines are ordered **by time**, not by id. This spot used
        # to check `ids == sorted(ids)` as well, but [6] just above pushes line
        # 1's start out to 50 s, so that demand runs against what store.cues()
        # promises -- a line a person wrote in (insert_cue) always gets the last
        # id while its time is somewhere in a gap, so handing them back in id
        # order throws off both the export order and the translation context
        # (store.cues docstring).
        ts = [c["t"] for c in store.cues(OWNER)]
        check(ts == sorted(ts), f"남은 줄은 시각순이다 ({ts})")
        check(not store.delete_cue(OWNER, 2), "이미 없는 줄은 False")

        print("\n[8] 기계 번역은 「원문과 다름」을 걷는다")
        store.replace_cues(OWNER + "-b", [
            {"start": 0.0, "end": 1.0, "text": "もと", "lang": "ja",
             "translations": {"g": "옛 번역"}},
        ])
        o2 = OWNER + "-b"
        store.edit_cue(o2, 1, text="고친 원문")
        check("text" in store.cues(o2)[0]["edited"], "원문을 고쳐 어긋남 표시를 만든다")
        store.save_translation(o2, 1, "g", "새 번역")
        got = store.cues(o2)[0]
        check("text" not in got["edited"],
              f"기계 번역 뒤 표시가 걷힌다 ({got['edited']!r})")
        check(got["translations"]["g"] == "새 번역", "번역이 갈렸다")
        # A machine translation does not erase the mark that a person edited it.
        store.edit_cue(o2, 1, tr="사람 번역", backend="g")
        store.save_translation(o2, 1, "g", "기계가 또 씀")
        check("tr" in store.cues(o2)[0]["edited"],
              "사람이 고쳤다는 표시는 기계 번역이 지우지 않는다")

        print("\n[9] 없는 것")
        check(store.edit_cue(OWNER, 999, text="x") is None, "없는 줄은 None")
        check(store.edit_cue("no-such-owner", 1, text="x") is None,
              "없는 소유자도 None")
        check(store.owner_of("live:abc") == "abc", "live: 접두어를 뗀다")
        check(store.owner_of("abc") == "abc", "영상 id 는 그대로")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}건: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
