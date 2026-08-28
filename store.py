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
  session  TEXT NOT NULL,
  cue_id   INTEGER NOT NULL,
  kind     TEXT NOT NULL DEFAULT '',
  t        REAL NOT NULL DEFAULT 0,
  text     TEXT NOT NULL DEFAULT '',
  lang     TEXT NOT NULL DEFAULT '',
  speaker  TEXT NOT NULL DEFAULT '',
  tr       TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (session, cue_id)
);
"""


def _connect() -> sqlite3.Connection:
    global _db
    if _db is None:
        os.makedirs(DATA, exist_ok=True)
        _db = sqlite3.connect(DB, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA synchronous=NORMAL")
        _db.execute("PRAGMA busy_timeout=5000")
        _db.executescript(SCHEMA)
        _db.commit()
    return _db


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


def sessions(limit: int = 30) -> list[dict]:
    out = []
    for r in _rows("SELECT s.doc, s.video_id, s.started, "
                   "  (SELECT COUNT(*) FROM cues WHERE cues.session = s.id) AS cues "
                   "FROM sessions s ORDER BY s.started DESC LIMIT ?", (limit,)):
        out.append({**json.loads(r["doc"]), "video_id": r["video_id"],
                    "started_at": r["started"], "cues": r["cues"]})
    return out


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
    _write("INSERT INTO cues (session, cue_id, kind, t, text, lang, speaker) "
           "VALUES (?, ?, ?, ?, ?, ?, ?) "
           "ON CONFLICT(session, cue_id) DO UPDATE SET "
           "  kind=excluded.kind, t=excluded.t, text=excluded.text, "
           "  lang=excluded.lang, speaker=excluded.speaker, tr='{}'",
           # `.get(k, "")`는 키가 없을 때만 기본값을 냅니다. 키가 있고
           # 값이 None이면 None이 그대로 바인딩되어, 열의 DEFAULT ''도
           # 적용되지 않은 채 NOT NULL에 걸립니다. 자막 한 줄을 잃는 것도
           # 아니고 세션이 끝나므로, 값 쪽에서 한 번 더 거릅니다.
           (session_id, int(cue["id"]), cue.get("kind") or "",
            float(cue.get("t") or 0), cue.get("text") or "",
            cue.get("lang") or "", cue.get("speaker") or ""))


def drop_cues(session_id: str, cue_ids: list[int]):
    if not cue_ids:
        return
    marks = ",".join("?" * len(cue_ids))
    _write(f"DELETE FROM cues WHERE session = ? AND cue_id IN ({marks})",
           (session_id, *[int(i) for i in cue_ids]))


def save_translation(session_id: str, cue_id: int, backend: str, text: str):
    # 읽고 고쳐 쓰는 것을 한 락 안에서 합니다. json_set()을 쓰면 한 문장이지만
    # SQLite 빌드에 JSON1이 있느냐에 달리게 되고, 그것은 이 도구가 굳이 질
    # 필요가 없는 의존입니다.
    with _lock:
        db = _connect()
        row = db.execute("SELECT tr FROM cues WHERE session = ? AND cue_id = ?",
                         (session_id, int(cue_id))).fetchone()
        if row is None:
            return
        tr = json.loads(row["tr"] or "{}")
        tr[backend] = text
        db.execute("UPDATE cues SET tr = ? WHERE session = ? AND cue_id = ?",
                   (json.dumps(tr, ensure_ascii=False), session_id, int(cue_id)))
        db.commit()


def cues(session_id: str) -> list[dict]:
    """쌓인 자막을 도착 순서대로. 정제본은 자기가 흡수한 첫 확정 줄의 id를
    물려받으므로, id 순서가 곧 읽는 순서입니다."""
    return [{"id": r["cue_id"], "kind": r["kind"], "t": r["t"],
             "text": r["text"], "lang": r["lang"], "speaker": r["speaker"],
             "translations": json.loads(r["tr"] or "{}")}
            for r in _rows("SELECT * FROM cues WHERE session = ? "
                           "ORDER BY cue_id", (session_id,))]
