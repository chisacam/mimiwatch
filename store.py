"""작업 상태와 라이브 자막의 영속화.

전사 결과(`data/<id>.json`)와 엔진 설정(`backends.json`)은 이미 파일로 남지만,
진행 중인 작업과 라이브 세션은 전부 프로세스 메모리에만 있었습니다. 녹화본
전사는 몇 분이라 다시 돌리면 그만이지만 라이브는 다릅니다. 두 시간짜리 방송을
받아 적던 중에 서버를 한 번 재시작하면 그때까지의 자막이 통째로 사라졌습니다.
브라우저에만 쌓여 있었으므로 탭을 새로고침해도 마찬가지였습니다.

**저장 형태**. 작업과 세션 레코드는 열로 펼치지 않고 JSON 한 덩어리로 넣습니다.
이 두 딕셔너리는 그대로 HTTP 응답 본문이 되므로, 열로 펼치면 필드가 하나 늘 때마다
스키마와 응답이 어긋날 자리가 생깁니다. 조회 조건이 id 하나뿐이라 펼쳐서 얻을
것도 없습니다. 반대로 자막은 정식 테이블입니다. 정제본이 확정 줄 여러 개를
흡수하면서 그 줄들을 지우고, 번역이 나중에 따로 도착하므로, 한 줄 단위로
갱신하고 지울 수 있어야 합니다.

**동시성**. 서버는 ThreadingHTTPServer이고, 라이브 세션은 자막 한 줄마다 번역
스레드를 새로 띄웁니다. 연결을 스레드별로 두면(threading.local) 그 수명이 1초도
안 되는 스레드마다 연결을 열게 되므로, **연결 하나를 락으로 감싸는 쪽**을
골랐습니다. 이 코드에는 트랜잭션을 오래 붙들고 있는 경로가 없어서 그래도
됩니다. 몇 시간씩 붙어 있는 SSE 핸들러조차 접속 순간에 백로그를 한 번 읽고 나면
DB를 건드리지 않습니다. 그래서 `check_same_thread=False`로 열고 모든 접근을
`_lock` 안에서 합니다.

WAL은 그래도 켭니다. 커밋마다 fsync를 하지 않아 자막 쓰기가 디스크를 기다리지
않고, 재시작이 이 파일의 존재 이유인 만큼 저널 방식이 중간에 끊겨도 복구되는
쪽이 낫습니다.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
DB = os.path.join(DATA, "mimiwatch.db")

_lock = threading.Lock()
_db: sqlite3.Connection | None = None

# 마이그레이션 단계를 사용자에게 시키지 않습니다. 시작할 때마다 IF NOT EXISTS로
# 만들고, 그것으로 끝입니다.
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id       TEXT PRIMARY KEY,
  updated  REAL NOT NULL,
  doc      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id        TEXT PRIMARY KEY,
  started   REAL NOT NULL,
  updated   REAL NOT NULL,
  video_id  TEXT NOT NULL DEFAULT '',
  doc       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cues (
  owner    TEXT NOT NULL,
  cue_id   INTEGER NOT NULL,
  kind     TEXT NOT NULL DEFAULT '',
  start    REAL NOT NULL DEFAULT 0,
  end      REAL NOT NULL DEFAULT 0,
  text     TEXT NOT NULL DEFAULT '',
  lang     TEXT NOT NULL DEFAULT '',
  speaker  TEXT NOT NULL DEFAULT '',
  tr       TEXT NOT NULL DEFAULT '{}',
  edited   TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (owner, cue_id)
);
CREATE INDEX IF NOT EXISTS cues_owner_start ON cues(owner, start);
CREATE TABLE IF NOT EXISTS docs (
  id       TEXT PRIMARY KEY,
  updated  REAL NOT NULL,
  doc      TEXT NOT NULL
);
"""

# 열 이름이 바뀌었습니다. 예전 판으로 만든 파일에는 `session`과 `t`가 있고,
# IF NOT EXISTS는 이미 있는 테이블을 그냥 두므로 여기서 따로 옮깁니다.
#
# `session`을 `owner`로 바꾼 것은 이 표가 이제 라이브 세션만 담지 않기
# 때문입니다. 녹화본 자막도 같은 표에 들어옵니다 -- 저장 모양이 갈려 있어서
# 자막 한 줄을 고치는 길이 두 벌이었고, 내보내기·편집·재번역이 그 갈래를
# 하나씩 더 짊어져야 했습니다.
#
# `t`를 `start`로 바꾼 것은 이제 짝이 되는 `end`가 생겼기 때문입니다.
# 녹화본은 구간을 실제로 재어 두었고, 라이브는 끝 시각을 모릅니다(0).
COLUMN_MOVES = [("session", "owner"), ("t", "start")]


def _connect() -> sqlite3.Connection:
    global _db
    if _db is None:
        os.makedirs(DATA, exist_ok=True)
        _db = sqlite3.connect(DB, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA synchronous=NORMAL")
        _db.execute("PRAGMA busy_timeout=5000")
        _migrate_columns(_db)
        _db.executescript(SCHEMA)
        _migrate_columns(_db)      # 갓 만든 표에는 걸릴 것이 없습니다
        _db.commit()
    return _db


def _migrate_columns(db: sqlite3.Connection):
    """옛 열 이름을 새 이름으로 옮깁니다. 이미 옮겼으면 아무것도 하지 않습니다."""
    have = {r[1] for r in db.execute("PRAGMA table_info(cues)")}
    if not have:
        return                      # 표가 아직 없습니다. SCHEMA가 만듭니다.
    for old, new in COLUMN_MOVES:
        if old in have and new not in have:
            db.execute(f"ALTER TABLE cues RENAME COLUMN {old} TO {new}")
            have.discard(old)
            have.add(new)
    if "end" not in have:
        # 이미 쌓인 자막은 전부 라이브라 끝 시각이 없습니다. 0은 「모른다」는
        # 뜻이고, 내보낼 때 다음 줄까지로 지어 줍니다.
        db.execute("ALTER TABLE cues ADD COLUMN end REAL NOT NULL DEFAULT 0")
    if "edited" not in have:
        # 사람이 손댄 자리를 적어 둡니다. 빈 문자열이면 기계가 적은 그대로.
        db.execute("ALTER TABLE cues ADD COLUMN edited TEXT NOT NULL DEFAULT ''")


def init():
    with _lock:
        _connect()


def _write(sql: str, args: tuple = ()):
    with _lock:
        db = _connect()
        db.execute(sql, args)
        db.commit()


def _rows(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return _connect().execute(sql, args).fetchall()


# ---- 작업 -----------------------------------------------------------------

def save_job(job: dict):
    _write("INSERT OR REPLACE INTO jobs (id, updated, doc) VALUES (?, ?, ?)",
           (job["id"], time.time(), json.dumps(job, ensure_ascii=False)))


def all_jobs() -> list[dict]:
    return [json.loads(r["doc"]) for r in
            _rows("SELECT doc FROM jobs ORDER BY updated")]


def prune_jobs(keep: int = 200):
    """작업 레코드는 진행률 표시용이라 오래된 것을 붙들 이유가 없습니다.
    자막과 달리 지워도 사용자가 잃는 것이 없으므로 여기만 상한을 둡니다."""
    _write("DELETE FROM jobs WHERE id NOT IN "
           "(SELECT id FROM jobs ORDER BY updated DESC LIMIT ?)", (keep,))


# ---- 라이브 세션 -----------------------------------------------------------

def save_session(status: dict, video_id: str = ""):
    now = time.time()
    _write("INSERT INTO sessions (id, started, updated, video_id, doc) "
           "VALUES (?, ?, ?, ?, ?) "
           "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, "
           # video_id는 방송 정보를 받아 오기 전에는 비어 있습니다. 나중에
           # 채워진 값을 빈 문자열로 되돌리지 않도록 있을 때만 덮어씁니다.
           "  video_id=CASE WHEN excluded.video_id != '' "
           "             THEN excluded.video_id ELSE sessions.video_id END, "
           "  doc=excluded.doc",
           (status["id"], now, now, video_id,
            json.dumps(status, ensure_ascii=False)))


def session(session_id: str) -> dict | None:
    rows = _rows("SELECT doc, video_id FROM sessions WHERE id = ?", (session_id,))
    if not rows:
        return None
    return {**json.loads(rows[0]["doc"]), "video_id": rows[0]["video_id"]}


def sessions(limit: int = 50) -> list[dict]:
    out = []
    for r in _rows("SELECT s.doc, s.video_id, s.started, "
                   "  (SELECT COUNT(*) FROM cues WHERE cues.owner = s.id) AS cues "
                   "FROM sessions s ORDER BY s.started DESC LIMIT ?", (limit,)):
        out.append({**json.loads(r["doc"]), "video_id": r["video_id"],
                    "started_at": r["started"], "cues": r["cues"]})
    return out


def delete_session(session_id: str) -> bool:
    """세션과 그 자막을 지웁니다. 받는 중인 세션을 막는 것은 live.py의 일입니다.

    예전에는 지울 길이 없어 목록이 자라기만 했습니다 -- 최근 20개만 보이는
    목록 뒤에 시험용 세션과 실패한 세션이 쌓여 진짜 방송이 밀려났습니다.
    """
    with _lock:
        db = _connect()
        db.execute("DELETE FROM cues WHERE owner = ?", (session_id,))
        cur = db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        db.commit()
        return cur.rowcount > 0


def running_session_ids() -> list[str]:
    return [r["id"] for r in _rows("SELECT id, doc FROM sessions")
            if json.loads(r["doc"]).get("state") in
            ("starting", "loading", "running")]


# ---- 라이브 자막 -----------------------------------------------------------

def save_cue(session_id: str, cue: dict):
    """확정 줄을 넣거나, 정제본으로 같은 id의 줄을 갈아 끼웁니다.

    갈아 끼울 때 번역은 비웁니다. 정제본은 문장이 바뀐 것이므로 앞 번역은
    이미 틀린 문장에 대한 번역입니다. 새 번역은 곧 따로 도착합니다.
    """
    _write("INSERT INTO cues (owner, cue_id, kind, start, end, text, lang, speaker) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
           "ON CONFLICT(owner, cue_id) DO UPDATE SET "
           "  kind=excluded.kind, start=excluded.start, end=excluded.end, "
           "  text=excluded.text, "
           "  lang=excluded.lang, speaker=excluded.speaker, tr='{}'",
           # `.get(k, "")`는 키가 없을 때만 기본값을 냅니다. 키가 있고
           # 값이 None이면 None이 그대로 바인딩되어, 열의 DEFAULT ''도
           # 적용되지 않은 채 NOT NULL에 걸립니다. 자막 한 줄을 잃는 것도
           # 아니고 세션이 끝나므로, 값 쪽에서 한 번 더 거릅니다.
           (session_id, int(cue["id"]), cue.get("kind") or "",
            float(cue.get("t") or 0), float(cue.get("end") or 0),
            cue.get("text") or "",
            cue.get("lang") or "", cue.get("speaker") or ""))


def drop_cues(session_id: str, cue_ids: list[int]):
    if not cue_ids:
        return
    marks = ",".join("?" * len(cue_ids))
    _write(f"DELETE FROM cues WHERE owner = ? AND cue_id IN ({marks})",
           (session_id, *[int(i) for i in cue_ids]))


def save_translation(session_id: str, cue_id: int, backend: str, text: str):
    # 읽고 고쳐 쓰는 것을 한 락 안에서 합니다. json_set()을 쓰면 한 문장이지만
    # SQLite 빌드에 JSON1이 있느냐에 달리게 되고, 그것은 이 도구가 굳이 질
    # 필요가 없는 의존입니다.
    with _lock:
        db = _connect()
        row = db.execute("SELECT tr, edited FROM cues WHERE owner = ? AND cue_id = ?",
                         (session_id, int(cue_id))).fetchone()
        if row is None:
            return
        tr = json.loads(row["tr"] or "{}")
        tr[backend] = text
        # 기계가 방금 번역했으니 「원문과 다름」 표시는 걷습니다. 이 번역은
        # 지금 있는 원문의 것입니다. 사람이 손댔다는 표시("tr")는 남깁니다 --
        # 그 줄은 애초에 재번역이 건너뛰므로 여기 오지 않지만, 라이브에서
        # 늦게 도착한 번역이 덮는 경우가 있습니다.
        flags = {f for f in (row["edited"] or "").split(",") if f} - {"text"}
        db.execute("UPDATE cues SET tr = ?, edited = ? "
                   "WHERE owner = ? AND cue_id = ?",
                   (json.dumps(tr, ensure_ascii=False), ",".join(sorted(flags)),
                    session_id, int(cue_id)))
        db.commit()


def cues(owner: str) -> list[dict]:
    """쌓인 자막을 도착 순서대로. 정제본은 자기가 흡수한 첫 확정 줄의 id를
    물려받으므로, id 순서가 곧 읽는 순서입니다.

    시작 시각은 `t`로 냅니다. 열 이름은 `start`이지만 이 딕셔너리는 그대로
    SSE 로 나가고 브라우저가 `t`로 읽습니다 -- 저장 이름을 바꿨다고 통신
    형식까지 흔들 이유가 없습니다. `end`는 0이면 모른다는 뜻입니다.
    """
    return [{"id": r["cue_id"], "kind": r["kind"], "t": r["start"],
             "end": r["end"],
             "text": r["text"], "lang": r["lang"], "speaker": r["speaker"],
             "edited": r["edited"],
             "translations": json.loads(r["tr"] or "{}")}
            for r in _rows("SELECT * FROM cues WHERE owner = ? "
                           "ORDER BY cue_id", (owner,))]


def cue_count(owner: str) -> int:
    return _rows("SELECT COUNT(*) AS n FROM cues WHERE owner = ?", (owner,))[0]["n"]


def replace_cues(owner: str, rows: list[dict]):
    """한 소유자의 자막을 통째로 갈아 끼웁니다. 녹화본 전사가 끝났을 때처럼
    앞의 것이 의미를 잃는 경우에만 씁니다."""
    with _lock:
        db = _connect()
        db.execute("DELETE FROM cues WHERE owner = ?", (owner,))
        # `edited`도 함께 씁니다. 빠뜨렸던 동안, 이 함수를 지나는 경로(엔진을
        # 바꿔 전체 번역, 같은 영상 다시 넣기)가 사람이 손댄 표시를 전부
        # 지웠습니다 -- 「원문과 다름」이 사라지고 손편집 번역이 뭉텅이
        # 재번역에 덮였습니다.
        db.executemany(
            "INSERT INTO cues (owner, cue_id, kind, start, end, text, lang, "
            "speaker, tr, edited) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(owner, i + 1, c.get("kind") or "final",
              float(c.get("start") or c.get("t") or 0),
              float(c.get("end") or 0), c.get("text") or "",
              c.get("lang") or "", c.get("speaker") or "",
              json.dumps(c.get("translations") or {}, ensure_ascii=False),
              c.get("edited") or "")
             for i, c in enumerate(rows)])
        db.commit()


def update_cue(owner: str, cue_id: int, **fields) -> bool:
    """자막 한 줄의 몇 칸만 고칩니다. 편집과 재번역이 쓰는 자리입니다.

    파일에 담아 두던 시절에는 한 글자를 고치려면 그 영상의 자막을 통째로
    다시 써야 했습니다. 83분짜리가 수백 줄인데, 그 도중에 서버가 죽으면
    전부 잃습니다.
    """
    cols = {k: v for k, v in fields.items()
            if k in ("kind", "start", "end", "text", "lang", "speaker", "edited")}
    tr = fields.get("translations")
    if not cols and tr is None:
        return False
    sets = [f"{k} = ?" for k in cols]
    args = list(cols.values())
    if tr is not None:
        sets.append("tr = ?")
        args.append(json.dumps(tr, ensure_ascii=False))
    with _lock:
        db = _connect()
        cur = db.execute(f"UPDATE cues SET {', '.join(sets)} "
                         "WHERE owner = ? AND cue_id = ?",
                         (*args, owner, int(cue_id)))
        db.commit()
        return cur.rowcount > 0


def owner_of(value: str) -> str:
    """화면의 목록이 쓰는 값(`live:<세션>` 또는 영상 id)을 표의 owner 로."""
    return value[5:] if value.startswith("live:") else value


# 사람이 손댄 자리. 빈 문자열이면 기계가 적은 그대로입니다.
#   "text" -- 원문을 고쳤습니다. 붙어 있는 번역은 **고치기 전 문장**의
#             번역이므로 더 이상 맞지 않습니다. 화면이 그렇게 표시합니다.
#   "tr"   -- 번역을 손으로 고쳤습니다. 뭉텅이 재번역이 이 줄을 덮으면
#             사람이 한 일이 지워지므로, 그때 건너뛰라는 표시입니다.
EDIT_FLAGS = ("text", "tr")


def edit_cue(owner: str, cue_id: int, *, text=None, tr=None, backend="",
             start=None, end=None) -> dict | None:
    """자막 한 줄을 사람이 고칩니다.

    돌려주는 것은 고쳐진 줄이고, 그 줄이 없으면 None 입니다.
    """
    with _lock:
        db = _connect()
        row = db.execute("SELECT * FROM cues WHERE owner = ? AND cue_id = ?",
                         (owner, int(cue_id))).fetchone()
        if row is None:
            return None
        flags = {f for f in (row["edited"] or "").split(",") if f}
        cols, args = [], []
        if text is not None and text != row["text"]:
            cols.append("text = ?")
            args.append(text)
            # 원문이 바뀌었으니 붙어 있는 번역은 옛 문장의 것입니다.
            flags.add("text")
        if tr is not None:
            trs = json.loads(row["tr"] or "{}")
            key = backend or next(iter(trs), "") or "manual"
            trs[key] = tr
            cols.append("tr = ?")
            args.append(json.dumps(trs, ensure_ascii=False))
            # 사람이 번역을 맞춰 두었으므로 어긋남 표시는 내려갑니다.
            flags.add("tr")
            flags.discard("text")
        if start is not None:
            new_start = float(start)
            cols.append("start = ?")
            args.append(new_start)
            # 끝 시각도 같이 옮깁니다. 시작만 옮기면 길이가 늘거나 줄고,
            # 앞으로 당긴 경우에는 끝이 시작보다 앞서는 자막이 나옵니다 --
            # SRT 로 내보내면 도구가 버리거나 통째로 무너집니다. 「이 줄을
            # 조금 앞으로」는 길이를 그대로 두고 옮기라는 뜻입니다.
            if end is None and row["end"] > row["start"]:
                cols.append("end = ?")
                args.append(new_start + (row["end"] - row["start"]))
        if end is not None:
            cols.append("end = ?")
            args.append(float(end))
        if not cols:
            return _row_to_cue(row)      # 바꿀 것이 없었습니다
        cols.append("edited = ?")
        args.append(",".join(sorted(flags)))
        db.execute(f"UPDATE cues SET {', '.join(cols)} "
                   "WHERE owner = ? AND cue_id = ?", (*args, owner, int(cue_id)))
        db.commit()
        got = db.execute("SELECT * FROM cues WHERE owner = ? AND cue_id = ?",
                         (owner, int(cue_id))).fetchone()
        return _row_to_cue(got)


def _row_to_cue(r) -> dict:
    return {"id": r["cue_id"], "kind": r["kind"], "t": r["start"], "end": r["end"],
            "text": r["text"], "lang": r["lang"], "speaker": r["speaker"],
            "edited": r["edited"], "translations": json.loads(r["tr"] or "{}")}


def delete_cue(owner: str, cue_id: int) -> bool:
    """줄 하나를 지웁니다. 번호는 다시 매기지 않습니다 -- 라이브에서는 정제본이
    자기가 흡수한 줄의 번호를 물려받으므로 번호가 곧 신원입니다."""
    with _lock:
        db = _connect()
        cur = db.execute("DELETE FROM cues WHERE owner = ? AND cue_id = ?",
                         (owner, int(cue_id)))
        db.commit()
        return cur.rowcount > 0


# ---- 녹화본 -----------------------------------------------------------------
#
# 예전에는 `data/<영상id>.json` 파일 하나에 메타와 자막을 함께 담았습니다.
# 자막만 이쪽 표로 옮기고 메타는 `docs`에 JSON 한 덩어리로 남깁니다 --
# 세션 레코드와 같은 이유입니다. 조회 조건이 id 하나뿐이고, 그대로 HTTP
# 응답이 되므로 열로 펼치면 필드가 늘 때마다 어긋날 자리가 생깁니다.

def save_doc(video_id: str, meta: dict):
    _write("INSERT OR REPLACE INTO docs (id, updated, doc) VALUES (?, ?, ?)",
           (video_id, time.time(), json.dumps(meta, ensure_ascii=False)))


def doc(video_id: str) -> dict | None:
    rows = _rows("SELECT doc FROM docs WHERE id = ?", (video_id,))
    return json.loads(rows[0]["doc"]) if rows else None


def doc_ids() -> list[str]:
    """최근에 손댄 것부터. 목록이 그 순서로 보여 줍니다."""
    return [r["id"] for r in
            _rows("SELECT id FROM docs ORDER BY updated DESC")]


def delete_doc(video_id: str):
    with _lock:
        db = _connect()
        db.execute("DELETE FROM cues WHERE owner = ?", (video_id,))
        db.execute("DELETE FROM docs WHERE id = ?", (video_id,))
        db.commit()


LEGACY = os.path.join(DATA, "legacy")


def import_legacy_docs() -> int:
    """`data/<영상id>.json` 을 표로 옮깁니다. 기동할 때 한 번 돌고, 옮길 것이
    없으면 아무것도 하지 않습니다.

    **원본을 지우지 않고 `data/legacy/` 로 옮깁니다.** 옮기는 일이 어긋나도
    되돌아갈 자리가 있어야 합니다. 그 디렉터리는 아무도 읽지 않으므로,
    한동안 써 보고 괜찮으면 지우면 됩니다.
    """
    if not os.path.isdir(DATA):
        return 0
    names = [n for n in sorted(os.listdir(DATA)) if n.endswith(".json")]
    if not names:
        return 0
    moved = 0
    for name in names:
        path = os.path.join(DATA, name)
        vid = name[:-5]
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        except Exception as exc:
            print(f"[store] {name} 을 읽지 못해 그대로 둡니다: {exc}")
            continue
        if not isinstance(old, dict) or "cues" not in old:
            continue                # 우리 것이 아닙니다
        rows = old.pop("cues", [])
        # 옛 판은 자막마다 `translation` 하나만 달고 있었습니다. 백엔드별
        # 지도로 접어 넣습니다 -- jobs.migrate() 가 하던 일입니다.
        for c in rows:
            if "translations" not in c:
                c["translations"] = ({"local-m2m100": c["translation"]}
                                     if c.get("translation") else {})
        old["backends_done"] = sorted({b for c in rows for b in c["translations"]})
        save_doc(vid, old)
        replace_cues(vid, rows)
        os.makedirs(LEGACY, exist_ok=True)
        os.replace(path, os.path.join(LEGACY, name))
        moved += 1
        print(f"[store] 옮김: {name} ({len(rows)}줄)")
    if moved:
        print(f"[store] 녹화본 {moved}개를 표로 옮겼습니다. "
              f"원본은 {LEGACY} 에 있습니다.")
    return moved
