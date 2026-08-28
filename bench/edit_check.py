"""자막을 손으로 고칠 때의 규칙을 확인합니다.

전사는 틀립니다. 잡음을 말로 듣고, 고유명사를 엉뚱하게 적고, 번역은 그
위에서 한 번 더 어긋납니다. 고치는 길에는 조용히 망가지는 자리가 몇 군데
있어서 그것들을 여기서 봅니다.

  - 원문을 고치면 붙어 있는 번역은 **옛 문장의 것**이 됩니다. 지우지 않고
    표시만 남겨야 하고, 번역을 손으로 맞추면 그 표시가 내려가야 합니다.
  - 뭉텅이 재번역이 사람이 고친 번역을 덮으면 안 됩니다. `edited` 에 남는
    표시가 그 판단의 근거입니다.
  - 시작 시각만 옮기면 끝 시각이 뒤에 남아 **끝이 시작보다 앞선** 자막이
    나옵니다. SRT 도구는 그런 줄을 버리거나 파일을 통째로 거부합니다.

    .venv/bin/python bench/edit_check.py

진짜 저장소를 건드리지 않습니다 -- 임시 사본에서만 씁니다.
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
        # 저장소를 직접 망가뜨려, edit_cue 를 거치지 않은 경로도 막히는지 봅니다.
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
        check(ids == sorted(ids) and 1 in ids and 3 in ids,
              f"남은 번호는 다시 매기지 않는다 ({ids})")
        check(not store.delete_cue(OWNER, 2), "이미 없는 줄은 False")

        print("\n[8] 없는 것")
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
