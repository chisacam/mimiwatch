"""Persistence for job state and live subtitles.

Transcription results (`data/<id>.json`) and engine settings (`backends.json`)
already survive as files, but running jobs and live sessions lived entirely in
process memory. A VOD transcription takes a few minutes, so re-running it costs
nothing; live is different. Restarting the server once while taking down a
two-hour broadcast lost every subtitle written until then. They had only ever
accumulated in the browser, so refreshing the tab did the same.

**Storage shape**. Job and session records go in as one JSON blob rather than
spread across columns. These two dictionaries become the HTTP response body as
they are, so spreading them across columns creates a place for the schema and
the response to disagree every time a field is added. The only query condition
is a single id, so there is nothing to gain by spreading them either.
Subtitles, by contrast, are a real table. A refined line absorbs several final
lines and deletes them, and its translation arrives separately later, so it has
to be possible to update and delete one line at a time.

**Concurrency**. The server is a ThreadingHTTPServer, and every live session
runs a translation job thread and a download thread of its own (it used to
spawn a thread per subtitle line). A connection per thread (threading.local)
would open one for every short-lived request thread, so **one connection
wrapped in a lock** was chosen instead. This code has no path that holds a
transaction open for long, which is what makes that acceptable. Even an SSE
handler attached for hours reads the backlog once at connect time and then
never touches the DB. So it is opened with `check_same_thread=False` and every
access happens inside `_lock`.

WAL is still turned on. It does not fsync on every commit, so a subtitle write
does not wait on the disk, and since restarts are the reason this file exists
at all, a journal mode that recovers from being cut off partway is the better
one.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

import paths

BASE = paths.BASE
# Where things are stored. `MIMIWATCH_DATA_DIR` overrides it -- when a test
# brings the server up in a temporary directory, or when a large wav belongs on
# another disk. Running from the repo it is `<repo>/data`; in a bundle it is the
# user area (paths.py).
DATA = paths.data_dir()
DB = os.path.join(DATA, "mimiwatch.db")

_lock = threading.Lock()
_db: sqlite3.Connection | None = None

# The user is never asked to run a migration step. Every start creates the
# tables with IF NOT EXISTS, and that is all.
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
CREATE TABLE IF NOT EXISTS glossaries (
  channel_key TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  terms       TEXT NOT NULL DEFAULT '[]',
  updated     REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS cues_fts USING fts5(
  owner UNINDEXED,
  cue_id UNINDEXED,
  text,
  tr_text
);
"""

# Column names changed. A file made by an older version has `session` and `t`,
# and IF NOT EXISTS leaves an existing table alone, so the move happens here.
#
# `session` became `owner` because this table no longer holds only live
# sessions. VOD subtitles come into the same table -- with the storage shape
# split, there were two ways to edit one subtitle line, and export, editing and
# re-translation each had to carry one more branch.
#
# `t` became `start` because it now has a matching `end`. A VOD's ranges were
# actually measured; live does not know the end time (0).
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
        _migrate_columns(_db)      # a freshly made table has nothing to move
        _db.commit()
    return _db


def _migrate_columns(db: sqlite3.Connection):
    """Move the old column names to the new ones. Does nothing once moved."""
    have = {r[1] for r in db.execute("PRAGMA table_info(cues)")}
    if not have:
        return                      # no table yet. SCHEMA creates it.
    for old, new in COLUMN_MOVES:
        if old in have and new not in have:
            db.execute(f"ALTER TABLE cues RENAME COLUMN {old} TO {new}")
            have.discard(old)
            have.add(new)
    if "end" not in have:
        # Every subtitle accumulated so far is live and has no end time. 0
        # means "unknown", and export synthesizes it up to the next line.
        db.execute("ALTER TABLE cues ADD COLUMN end REAL NOT NULL DEFAULT 0")
    if "edited" not in have:
        # Records where a person edited. Empty means as the machine wrote it.
        db.execute("ALTER TABLE cues ADD COLUMN edited TEXT NOT NULL DEFAULT ''")


def init():
    with _lock:
        db = _connect()
        _fts_resync(db)


def _write(sql: str, args: tuple = ()):
    with _lock:
        db = _connect()
        db.execute(sql, args)
        db.commit()


def _rows(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return _connect().execute(sql, args).fetchall()


# ---- Jobs ------------------------------------------------------------------

def save_job(job: dict):
    _write("INSERT OR REPLACE INTO jobs (id, updated, doc) VALUES (?, ?, ?)",
           (job["id"], time.time(), json.dumps(job, ensure_ascii=False)))


def all_jobs() -> list[dict]:
    return [json.loads(r["doc"]) for r in
            _rows("SELECT doc FROM jobs ORDER BY updated")]


def prune_jobs(keep: int = 200):
    """Job records exist to show progress, so there is no reason to hold on to
    old ones. Unlike subtitles, deleting them costs the user nothing, so this
    is the only place with a cap."""
    _write("DELETE FROM jobs WHERE id NOT IN "
           "(SELECT id FROM jobs ORDER BY updated DESC LIMIT ?)", (keep,))


# ---- Live sessions ---------------------------------------------------------

def save_session(status: dict, video_id: str = ""):
    now = time.time()
    _write("INSERT INTO sessions (id, started, updated, video_id, doc) "
           "VALUES (?, ?, ?, ?, ?) "
           "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, "
           # video_id is empty until the broadcast metadata arrives. It is
           # overwritten only when present, so a value filled in later is not
           # reset to an empty string.
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
    """Delete a session and its subtitles. Blocking a session that is still
    being received is live.py's job.

    There used to be no way to delete, so the list only grew -- test sessions
    and failed sessions piled up behind a list that shows only the most recent
    20, and the real broadcasts were pushed out.
    """
    with _lock:
        db = _connect()
        db.execute("DELETE FROM cues WHERE owner = ?", (session_id,))
        _fts_sync(db, session_id)
        cur = db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        db.commit()
        return cur.rowcount > 0


def running_session_ids() -> list[str]:
    return [r["id"] for r in _rows("SELECT id, doc FROM sessions")
            if json.loads(r["doc"]).get("state") in
            ("starting", "loading", "running")]


# ---- Live subtitles --------------------------------------------------------

def save_cue(session_id: str, cue: dict):
    """Insert a final line, or replace the line with the same id with a
    refined one.

    The translation is cleared on replacement. A refined line is a changed
    sentence, so the earlier translation is a translation of a sentence that is
    already wrong. The new translation arrives separately, soon.
    """
    # The insert and the FTS resync sit in one lock: the connection is a
    # shared object (check_same_thread=False), and a resync running outside
    # the lock is a use the translation loop's locked write can land inside
    # -- the same two-handed use of one connection sqlite answers with a
    # "bad parameter" InterfaceError, which ended live sessions in the test
    # suite's flake.
    with _lock:
        db = _connect()
        db.execute("INSERT INTO cues (owner, cue_id, kind, start, end, text, lang, speaker) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT(owner, cue_id) DO UPDATE SET "
                   "  kind=excluded.kind, start=excluded.start, end=excluded.end, "
                   "  text=excluded.text, "
                   "  lang=excluded.lang, speaker=excluded.speaker, tr='{}'",
                   # `.get(k, "")` yields the default only when the key is missing.
                   # With the key present and the value None, None binds as it is,
                   # the column's DEFAULT '' does not apply either, and it hits NOT
                   # NULL. That does not cost one subtitle line, it ends the session,
                   # so the values are filtered once more here.
                   (session_id, int(cue["id"]), cue.get("kind") or "",
                    float(cue.get("t") or 0), float(cue.get("end") or 0),
                    cue.get("text") or "",
                    cue.get("lang") or "", cue.get("speaker") or ""))
        _fts_sync(db, session_id)
        db.commit()


def drop_cues(session_id: str, cue_ids: list[int]):
    if not cue_ids:
        return
    marks = ",".join("?" * len(cue_ids))
    # One lock around the delete and the resync, for the same reason as
    # save_cue: the shared connection is not to be used from two places at
    # once, locked or not.
    with _lock:
        db = _connect()
        db.execute(f"DELETE FROM cues WHERE owner = ? AND cue_id IN ({marks})",
                   (session_id, *[int(i) for i in cue_ids]))
        _fts_sync(db, session_id)
        db.commit()


def save_translation(session_id: str, cue_id: int, backend: str, text: str):
    # The read-modify-write happens inside one lock. json_set() would make it
    # a single statement but would depend on whether the SQLite build has
    # JSON1, and that is a dependency this tool has no need to take on.
    with _lock:
        db = _connect()
        row = db.execute("SELECT tr, edited FROM cues WHERE owner = ? AND cue_id = ?",
                         (session_id, int(cue_id))).fetchone()
        if row is None:
            return
        tr = json.loads(row["tr"] or "{}")
        tr[backend] = text
        # The machine just translated, so the "differs from the source" mark
        # comes off. This translation belongs to the source text as it now
        # stands. The hand-edited mark ("tr") stays -- re-translation skips
        # such a line in the first place so it does not come here, but in live
        # a translation arriving late does overwrite it.
        flags = {f for f in (row["edited"] or "").split(",") if f} - {"text"}
        db.execute("UPDATE cues SET tr = ?, edited = ? "
                   "WHERE owner = ? AND cue_id = ?",
                   (json.dumps(tr, ensure_ascii=False), ",".join(sorted(flags)),
                    session_id, int(cue_id)))
        _fts_sync(db, session_id)
        db.commit()


def cues(owner: str) -> list[dict]:
    """The accumulated subtitles, **in time order**. The number is identity,
    not order.

    For lines transcription made, number order and time order agree (a refined
    line inherits the id and the time of the first line it absorbed), but a
    line a person wrote in (insert_cue) always has the last number while its
    time sits somewhere in an empty gap. Sorting by number sticks that line on
    the end and throws off the export order, the translation context (the
    preceding few lines) and the time lookup on screen, all of them. The number
    is used only as a deterministic order within the same time.

    The start time comes out as `t`. The column is named `start`, but this
    dictionary goes out over SSE as it is and the browser reads `t` -- renaming
    the storage is no reason to shake the wire format too. An `end` of 0 means
    unknown.
    """
    return [{"id": r["cue_id"], "kind": r["kind"], "t": r["start"],
             "end": r["end"],
             "text": r["text"], "lang": r["lang"], "speaker": r["speaker"],
             "edited": r["edited"],
             "translations": json.loads(r["tr"] or "{}")}
            for r in _rows("SELECT * FROM cues WHERE owner = ? "
                           "ORDER BY start, cue_id", (owner,))]


def cue_count(owner: str) -> int:
    return _rows("SELECT COUNT(*) AS n FROM cues WHERE owner = ?", (owner,))[0]["n"]


def replace_cues(owner: str, rows: list[dict]):
    """Replace one owner's subtitles wholesale. Used only when what came
    before has lost its meaning, as when a VOD transcription finishes."""
    with _lock:
        db = _connect()
        db.execute("DELETE FROM cues WHERE owner = ?", (owner,))
        # `edited` is written along with the rest. While it was left out, the
        # paths through this function (translate everything after switching
        # engines, re-adding the same video) erased every hand-edit mark --
        # "differs from the source" disappeared and hand-edited translations
        # were overwritten by bulk re-translation.
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
        _fts_sync(db, owner)
        db.commit()


def update_cue(owner: str, cue_id: int, **fields) -> bool:
    """Change only a few fields of one subtitle line. This is what editing and
    re-translation use.

    Back when this was kept in a file, changing one character meant rewriting
    that video's subtitles wholesale. An 83-minute video is several hundred
    lines, and if the server dies partway through, all of it is lost.
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
        # Only fields the search reads are worth a resync. A timing fix
        # (start/end) moves a line, it does not change what it says.
        if cur.rowcount > 0 and ("text" in cols or tr is not None):
            _fts_sync(db, owner)
        return cur.rowcount > 0


def owner_of(value: str) -> str:
    """The value the on-screen list uses (`live:<session>` or a video id), as
    the table's owner."""
    return value[5:] if value.startswith("live:") else value


# Where a person edited. An empty string means as the machine wrote it.
#   "text" -- the source text was edited. The translation attached to it is a
#             translation of **the sentence before the edit**, so it no longer
#             matches. The screen says so.
#   "tr"   -- the translation was edited by hand. If bulk re-translation
#             overwrote this line, the person's work would be erased, so this
#             is the mark that tells it to skip.
EDIT_FLAGS = ("text", "tr")


def edit_cue(owner: str, cue_id: int, *, text=None, tr=None, backend="",
             start=None, end=None) -> dict | None:
    """A person edits one subtitle line.

    Returns the edited line, or None when there is no such line.
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
            # The source changed, so the translation attached to it belongs
            # to the old sentence.
            flags.add("text")
        if tr is not None:
            trs = json.loads(row["tr"] or "{}")
            key = backend or next(iter(trs), "") or "manual"
            trs[key] = tr
            cols.append("tr = ?")
            args.append(json.dumps(trs, ensure_ascii=False))
            # The person has matched the translation up, so the mismatch
            # mark comes down.
            flags.add("tr")
            flags.discard("text")
        if start is not None:
            new_start = float(start)
            cols.append("start = ?")
            args.append(new_start)
            # The end time moves with it. Moving only the start grows or
            # shrinks the duration, and pulling it earlier produces a subtitle
            # whose end precedes its start -- exported as SRT, a tool either
            # discards it or falls over wholesale. "This line a little
            # earlier" means move it and leave the duration alone.
            if end is None and row["end"] > row["start"]:
                cols.append("end = ?")
                args.append(new_start + (row["end"] - row["start"]))
        if end is not None:
            cols.append("end = ?")
            args.append(float(end))
        if not cols:
            return _row_to_cue(row)      # there was nothing to change
        cols.append("edited = ?")
        args.append(",".join(sorted(flags)))
        db.execute(f"UPDATE cues SET {', '.join(cols)} "
                   "WHERE owner = ? AND cue_id = ?", (*args, owner, int(cue_id)))
        # cols here is the list of `col = ?` clauses, so the fields are written out.
        if "text = ?" in cols or "tr = ?" in cols:
            _fts_sync(db, owner)
        db.commit()
        got = db.execute("SELECT * FROM cues WHERE owner = ? AND cue_id = ?",
                         (owner, int(cue_id))).fetchone()
        return _row_to_cue(got)


def _row_to_cue(r) -> dict:
    return {"id": r["cue_id"], "kind": r["kind"], "t": r["start"], "end": r["end"],
            "text": r["text"], "lang": r["lang"], "speaker": r["speaker"],
            "edited": r["edited"], "translations": json.loads(r["tr"] or "{}")}


def insert_cue(owner: str, start: float, text: str, *, lang: str = "",
               tr: str = "", backend: str = "", end: float = 0.0) -> dict:
    """A person writes in a new subtitle line.

    A line that gets deleted is usually a misrecognition (hearing a sound
    effect as speech, say), and the speech missed in the process is left as an
    empty stretch of time. This is the way to fill that place.

    The number is the one after this owner's last -- the number is only
    identity, and cues() sorts the reading order by time. When a translation is
    written along with it, it is marked hand-edited ("tr") so that bulk
    re-translation does not overwrite it. When only the source text is written,
    it is left unmarked -- machine translation has to be able to attach, and an
    attached translation belongs to this source text, so there is no reason for
    the "differs from the source" mark to stand either.
    """
    with _lock:
        db = _connect()
        cue_id = int(db.execute("SELECT COALESCE(MAX(cue_id), 0) + 1 AS n "
                                "FROM cues WHERE owner = ?", (owner,)).fetchone()["n"])
        trs = {backend or "manual": tr} if tr else {}
        db.execute("INSERT INTO cues (owner, cue_id, kind, start, end, text, lang, "
                   "speaker, tr, edited) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (owner, cue_id, "final", float(start), float(end or 0), text,
                    lang, "", json.dumps(trs, ensure_ascii=False), "tr" if tr else ""))
        _fts_sync(db, owner)
        db.commit()
        got = db.execute("SELECT * FROM cues WHERE owner = ? AND cue_id = ?",
                         (owner, cue_id)).fetchone()
        return _row_to_cue(got)


def delete_cue(owner: str, cue_id: int) -> bool:
    """Delete one line. The numbers are not reassigned -- in live a refined
    line inherits the number of the line it absorbed, so the number is
    identity."""
    with _lock:
        db = _connect()
        cur = db.execute("DELETE FROM cues WHERE owner = ? AND cue_id = ?",
                         (owner, int(cue_id)))
        _fts_sync(db, owner)
        db.commit()
        return cur.rowcount > 0


# ---- VOD -------------------------------------------------------------------
#
# It used to be one `data/<video id>.json` file holding the metadata and the
# subtitles together. Only the subtitles moved to this table; the metadata
# stays in `docs` as one JSON blob -- the same reason as for the session
# records. The only query condition is a single id, and it becomes the HTTP
# response as it is, so spreading it across columns creates a place to
# disagree every time a field is added.

def save_doc(video_id: str, meta: dict):
    _write("INSERT OR REPLACE INTO docs (id, updated, doc) VALUES (?, ?, ?)",
           (video_id, time.time(), json.dumps(meta, ensure_ascii=False)))


def doc(video_id: str) -> dict | None:
    rows = _rows("SELECT doc FROM docs WHERE id = ?", (video_id,))
    return json.loads(rows[0]["doc"]) if rows else None


def doc_ids() -> list[str]:
    """Most recently touched first. The list shows them in that order."""
    return [r["id"] for r in
            _rows("SELECT id FROM docs ORDER BY updated DESC")]


def delete_doc(video_id: str):
    with _lock:
        db = _connect()
        db.execute("DELETE FROM cues WHERE owner = ?", (video_id,))
        _fts_sync(db, video_id)
        db.execute("DELETE FROM docs WHERE id = ?", (video_id,))
        db.commit()


# ---- Glossaries --------------------------------------------------------------

def channel_key(site: str, channel: str, manual_name: str = "") -> str:
    """The key a glossary is looked up by. The rule lives in one place, so the
    web, the jobs and the live sessions all derive the same key.

    An extracted channel keys as `{site}:{channel}` (youtube:UC…, twitch:login);
    a name a person typed when the extraction was empty keys as `manual:{name}`.
    Neither present means no key, and the job matches no glossary -- which the
    UI shows, instead of letting the miss stay silent.
    """
    if (channel or "").strip():
        return f"{site or 'other'}:{channel.strip()}"
    if (manual_name or "").strip():
        return "manual:" + manual_name.strip()
    return ""


def glossary(channel_key: str) -> dict | None:
    rows = _rows("SELECT channel_key, name, terms, updated FROM glossaries "
                 "WHERE channel_key = ?", (channel_key or "",))
    if not rows:
        return None
    r = rows[0]
    return {"channel_key": r["channel_key"], "name": r["name"],
            "terms": json.loads(r["terms"]), "updated": r["updated"]}


def all_glossaries() -> list[dict]:
    out = []
    for r in _rows("SELECT channel_key, name, terms, updated FROM glossaries "
                   "ORDER BY name"):
        out.append({"channel_key": r["channel_key"], "name": r["name"],
                    "terms": json.loads(r["terms"]),
                    "updated": r["updated"]})
    return out


def save_glossary(channel_key: str, name: str, terms: list) -> dict | None:
    """Create or replace a glossary. An empty term list deletes the row --
    the editor saves "nothing" as a removal instead of a second button."""
    channel_key = (channel_key or "").strip()
    if not channel_key:
        return None
    if not terms:
        _write("DELETE FROM glossaries WHERE channel_key = ?", (channel_key,))
        return None
    _write(
        "INSERT INTO glossaries (channel_key, name, terms, updated) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(channel_key) DO UPDATE SET "
        "name = excluded.name, terms = excluded.terms, "
        "updated = excluded.updated",
        (channel_key, (name or "").strip(),
         json.dumps(terms, ensure_ascii=False), time.time()))
    return glossary(channel_key)


# ---- Full-text search ----------------------------------------------------------
#
# `cues_fts` is an ordinary FTS5 table, not an external-content one. An
# external-content table trusts that the table it mirrors stays in step, and
# keeping that promise row by row is more code than it is worth. Every cue
# write path resyncs its owner's whole set instead: an owner's cues are at
# most a few thousand rows, a rebuild is cheap, and a drifted table would
# cost a wrong search answer. The `tr` column holds a JSON object keyed by
# backend, so the translation side is flattened to one string with Python's
# json, the same way the rest of this file treats the column.

def _fts_sync(db: sqlite3.Connection, owner: str) -> None:
    """Put one owner's cues back into the FTS table. The caller holds the lock.

    It does not commit: the write that triggered it commits, and a start-up
    resync commits for all of them at once."""
    db.execute("DELETE FROM cues_fts WHERE owner = ?", (owner,))
    rows = db.execute(
        "SELECT cue_id, text, tr FROM cues WHERE owner = ?",
        (owner,)).fetchall()
    db.executemany(
        "INSERT INTO cues_fts (owner, cue_id, text, tr_text) VALUES (?,?,?,?)",
        [(owner, r["cue_id"], r["text"] or "",
          " ".join(str(v) for v in (json.loads(r["tr"] or "{}").values() or []) if v))
         for r in rows])


def _fts_resync(db: sqlite3.Connection) -> None:
    """Wipe the whole FTS table and rebuild it from the cues. The caller holds
    the lock.

    It runs once at every start: the table a write cut short, or a version
    that did not know the table at all, left half stale cannot outlive one
    restart. The cost is the size of the subtitle table, which is small."""
    db.execute("DELETE FROM cues_fts")
    owners = [r["owner"] for r in db.execute("SELECT DISTINCT owner FROM cues")]
    for owner in owners:
        _fts_sync(db, owner)
    db.commit()


def search(q: str, limit: int = 50) -> list[dict]:
    """Full-text search over the subtitle source text and its translations.

    The query is free text, not FTS5 syntax: each whitespace-separated word
    is quoted and the words stand in AND. A word that contains a quote is
    doubled inside, the way a quoted FTS5 term says a literal quote, so
    nothing the user types can break the statement."""
    words = ['"' + w.replace('"', '""') + '"' for w in (q or "").split()]
    if not words:
        return []
    # The time and the text come back from the cues table: the FTS table holds
    # the words, the cues table holds the shape of the line.
    rows = _rows(
        "SELECT c.owner, c.cue_id, c.start, c.text, "
        "snippet(cues_fts, 2, '«', '»', '…', 8) AS snip, "
        "snippet(cues_fts, 3, '«', '»', '…', 8) AS snip_tr "
        "FROM cues_fts JOIN cues c ON c.owner = cues_fts.owner "
        " AND c.cue_id = cues_fts.cue_id "
        "WHERE cues_fts MATCH ? ORDER BY rank LIMIT ?",
        (" ".join(words), limit))
    return [dict(r) for r in rows]


def owner_kind(owner: str) -> str:
    """Whether the owner of a subtitle set is a live session or a video.

    The on-screen list writes the two kinds apart (`live:<session>` and a
    video id); the table keeps the bare owner, so this is what tells them
    back apart."""
    if _rows("SELECT 1 FROM sessions WHERE id = ?", (owner,)):
        return "live"
    return "video"


def owner_title(owner: str) -> str:
    """The title the screen has for a subtitle set's owner. A live session
    falls back to its address, a video to its id."""
    r = _rows("SELECT doc FROM sessions WHERE id = ?", (owner,))
    if r:
        d = json.loads(r[0]["doc"] or "{}")
        return d.get("title") or d.get("url") or owner
    r = _rows("SELECT doc FROM docs WHERE id = ?", (owner,))
    if r:
        d = json.loads(r[0]["doc"] or "{}")
        return d.get("title") or owner
    return owner


LEGACY = os.path.join(DATA, "legacy")


def import_legacy_docs() -> int:
    """Move `data/<video id>.json` into the table. Runs once at startup, and
    does nothing when there is nothing to move.

    **The originals are moved to `data/legacy/` rather than deleted.** If the
    move goes wrong, there has to be somewhere to go back to. Nobody reads
    that directory, so it can be deleted once it has been used for a while
    and seems fine.
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
            print(f"[store] cannot read {name}, leaving it as it is: {exc}")
            continue
        if not isinstance(old, dict) or "cues" not in old:
            continue                # not ours
        rows = old.pop("cues", [])
        # The old version carried only one `translation` per subtitle. It is
        # folded into a per-backend map -- what jobs.migrate() used to do.
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
        print(f"[store] moved: {name} ({len(rows)} lines)")
    if moved:
        print(f"[store] moved {moved} VOD(s) into the table. "
              f"The originals are in {LEGACY}.")
    return moved
