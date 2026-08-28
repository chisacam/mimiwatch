"""저장소를 SQLite로 통일한 것이 데이터를 그대로 옮겼는지 확인합니다.

녹화본은 `data/<영상id>.json` 파일 하나에 메타와 자막을 함께 담고 있었고,
라이브는 SQLite에 있었습니다. 저장 모양이 갈려 있으니 읽고 쓰는 길이 두
벌이었고, 내보내기·편집·재번역이 그 갈래를 하나씩 더 짊어져야 했습니다.

이 시험은 **옮기는 코드가 한 글자도 잃지 않는지**를 봅니다. 옛 판이 남기던
모양의 파일을 여기서 지어 임시 저장소로 옮겨 보고, 그 결과를 지은 것과
맞댑니다.

실제 저장소와 맞대지 않는 이유가 있습니다. 옮긴 뒤에도 사람은 자막을 고치고
지우고 다시 전사합니다 -- 그때마다 실제 저장소는 원본과 달라지는 것이
당연하고, 그것을 실패로 적으면 정상적인 작업이 시험을 깨뜨립니다. 실제로
한 번 그렇게 울렸습니다.

표본을 여기서 짓는 이유도 같습니다. `data/legacy/` 에 기대면 그 폴더를
치우는 순간(치워도 되는 폴더입니다) 시험이 아무것도 보지 않게 됩니다.

    .venv/bin/python bench/store_migrate.py

저장소에 쓰지 않습니다 -- 읽기만 하고, 쓰기 왕복은 임시 사본에서 합니다.
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
    """빈 칸은 있으나 없으나 같습니다. 옛 파일은 speaker 를 아예 안 적었고,
    표는 빈 문자열로 둡니다."""
    return {k: v for k, v in cue.items() if v not in ("", {}, None)}


def main():
    print("\n[1] 스키마")
    import sqlite3
    db = sqlite3.connect(f"file:{store.DB}?mode=ro", uri=True)
    cols = [r[1] for r in db.execute("PRAGMA table_info(cues)")]
    for c in ("owner", "start", "end"):
        check(c in cols, f"cues.{c} 가 있다")
    for gone in ("session", "t"):
        check(gone not in cols, f"옛 이름 cues.{gone} 는 없다")
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check("docs" in tables, "docs 표가 있다")
    idx = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    check("cues_owner_start" in idx, "owner+start 인덱스가 있다")

    print("\n[2] 옮기는 코드가 원본을 그대로 옮기는가")
    # 옛 판이 남기던 모양 그대로 짓습니다. 마지막 것은 자막마다 translation
    # 하나만 달던 더 옛 판입니다 -- 그 접기까지 함께 봅니다.
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
        check(moved == len(FIXTURES), f"{len(FIXTURES)}개를 옮겼다 ({moved})")
        for vid, doc in FIXTURES.items():
            old = json.loads(json.dumps(doc))      # 원본을 건드리지 않습니다
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
                  f"{vid}: 자막 {len(old_cues)}줄과 메타 {len(keys)}칸이 그대로"
                  + ("" if same_n else f" (줄 수 {len(old_cues)}->{len(new_cues)})")
                  + ("" if same_c or not same_n else " (내용 다름)")
                  + ("" if same_m else " (메타 다름)"))
        # 옛 판의 translation 접기
        got = store.cues("vid-old")
        check(got[0]["translations"] == {"local-m2m100": "하나"},
              f"옛 translation 을 백엔드 지도로 접는다 ({got[0]['translations']})")
        check(got[1]["translations"] == {},
              "빈 translation 은 빈 지도가 된다")
        check(store.doc("vid-old")["backends_done"] == ["local-m2m100"],
              "backends_done 을 자막에서 다시 센다")
        check(not [n for n in os.listdir(os.path.join(tmp, "data"))
                   if n.endswith(".json")], "옮긴 원본은 data/ 에 남지 않는다")
        check(len(os.listdir(store.LEGACY)) == len(FIXTURES),
              "원본은 legacy/ 로 옮겨진다 (지우지 않습니다)")
        check(store.import_legacy_docs() == 0, "다시 불러도 옮길 것이 없다")
    finally:
        store.DATA, store.DB, store.LEGACY, store._db = real
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[3] 라이브 자막은 그대로인가")
    sess = [s for s in store.sessions(200) if s.get("cues")]
    check(len(sess) > 0, f"자막이 있는 세션 {len(sess)}개")
    for s in sess[:3]:
        cs = store.cues(s["id"])
        check(len(cs) == s["cues"], f"{s['id']}: 세는 수와 읽는 수가 같다 "
                                    f"({s['cues']} / {len(cs)})")
        check(all("t" in c for c in cs),
              f"{s['id']}: 시작 시각 키가 t 로 나온다 (통신 형식 유지)")

    print("\n[4] 자막 한 줄만 고치기 (사본에서)")
    tmp = tempfile.mkdtemp(prefix="mw-store-")
    try:
        # WAL 도 같이 옮깁니다. 본 파일만 베끼면 아직 체크포인트되지 않은
        # 것이 통째로 빠져, 방금 넣은 녹화본이 없는 DB를 보게 됩니다.
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
            check(False, f"사본에서 돌리지 못했습니다: {out.stderr[-300:]}")
        else:
            r = json.loads(line[-1])
            check(r["ok"], "update_cue 가 한 줄을 고쳤다")
            check(r["n0"] == r["n1"], f"줄 수가 그대로 ({r['n0']})")
            check(r["changed"] == "바꾼 줄", "고친 줄이 반영됐다")
            check(r["neighbour_same"], "앞뒤 줄은 건드리지 않았다")
            check(r["tr_kept"], "그 줄의 번역은 남아 있다")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}건: " + "; ".join(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
