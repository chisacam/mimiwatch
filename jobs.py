"""Background jobs: VOD transcription and (re)translation.

Translation started out as something done once during transcription and baked
into the subtitle file. Comparing engines requires being able to translate an
already-transcribed video again, so translations are held separately per engine
id, the job runs on a background thread, and the viewer keeps watching.

**There is one translate loop (`_translate_rows`).** There used to be three --
translate everything, re-translate a selection, translate right after
transcription. Only one of the three skipped hand-edited translations; the
other two rewrote the subtitles wholesale and erased the `edited` marks along
with them. Merely switching engines overwrote a translation a person had
matched up and made the "differs from the source" mark disappear. Now all three
go through the same loop, and that loop writes one line at a time with
`store.save_translation`.

`config.py` handles the settings. Names like `load_config` left here are only
so the callers stay as they are.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid

import bus
import burn
import config
import export
import translate as mw_translate
import transcribe_vod as vod
import asr as mw_asr
import stream as mw_stream
import live
import outline as mw_outline
import store

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = store.DATA                 # the downloaded wav goes where the subtitles go

# Moved to config.py. server.py and the tests call them by these names.
load_config = config.load
save_config = config.save
find_backend = config.find_backend
find_asr = config.find_asr
PROTECTED = config.PROTECTED

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
# Job id → the thread running that job. Shutdown uses it to wait for the
# thread to end (wait_idle).
_threads: dict[str, threading.Thread] = {}


def _note(job_id: str, **kw):
    """Update the progress state and push it to disk right away.

    The in-memory copy could be dropped so that only SQLite is consulted, but
    this is a path polled every 0.7 seconds, so answering a query from memory
    is better. The write covers the whole record, so a counter incremented
    inside the lock in the meantime rides out along with it.
    """
    with _lock:
        j = _jobs.get(job_id)
        if j is None:
            return
        j.update(kw)
        snapshot = dict(j)
    store.save_job(snapshot)
    # Other windows watch this job's progress too. The window that started it
    # sees it by polling as well, but for a job started from the extension or
    # from another tab this is the only way.
    bus.publish({"type": "job", **snapshot})


def _new_job(**fields) -> str:
    """Make a job record, put it in memory and in the table, return the id."""
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "running", "done": 0, "total": 0,
           "started": time.time(), "error": None, "skipped": 0, "kept": 0,
           "degraded": False, "failures": 0,
           # Counted per line. This is to show in numbers which model
           # actually produced the text -- not "a fallback happened at some
           # point".
           "by_remote": 0, "by_local": 0, "cancel": False}
    job.update(fields)
    with _lock:
        _jobs[job_id] = job
        snapshot = dict(job)
    store.save_job(snapshot)
    bus.publish({"type": "job", **snapshot})
    return job_id


def restore() -> int:
    """Restore the jobs that were running before the restart -- the state only.

    The thread that drove a job went away with the process, so that
    transcription does not carry on. Left as `running`, the UI would keep
    polling something that will never end, so it is honestly recorded as
    interrupted. How far it had got stays as it was.
    """
    hit = 0
    with _lock:
        for job in store.all_jobs():
            if job.get("state") == "running":
                job["state"] = "interrupted"
                job["error"] = "interrupted because the server restarted"
                hit += 1
            _jobs[job["id"]] = job
    for job in list(_jobs.values()):
        if job.get("state") == "interrupted":
            store.save_job(job)
    store.prune_jobs()
    return hit


# VODs go in the same table as live. It used to be one `data/<video id>.json`
# file, and then every edit to one subtitle line meant rewriting that video's
# subtitles wholesale. An 83-minute video is several hundred lines, and dying
# partway through the write loses all of it. The three below are shells whose
# names remain so the callers stay as they are.

def has_video(vid: str) -> bool:
    return store.doc(vid) is not None


def load_video(vid: str) -> dict:
    meta = store.doc(vid)
    if meta is None:
        raise KeyError(vid)
    # The id comes out with it. Editing one subtitle line requires the screen
    # to be able to point at that line, and a position (index) is thrown off by
    # deleting a single line.
    cues = [{"id": c["id"], "start": c["t"], "end": c["end"], "lang": c["lang"],
             "text": c["text"], "translations": c["translations"],
             "edited": c["edited"]}
            | ({"speaker": c["speaker"]} if c["speaker"] else {})
            for c in store.cues(vid)]
    doc = {**meta, "cues": cues}
    doc["backends_done"] = sorted({b for c in cues for b in c["translations"]})
    return doc


def save_video(vid: str, doc: dict):
    """Write the transcription result wholesale. Called **only once the
    transcription has finished** -- it replaces every subtitle, so it is not
    used for work that changes a few lines, like translation or editing. That
    is `store.save_translation`/`store.edit_cue`."""
    doc = dict(doc)
    cues = doc.pop("cues", [])
    doc["backends_done"] = sorted({b for c in cues
                                   for b in c.get("translations", {})})
    store.save_doc(vid, doc)
    store.replace_cues(vid, cues)
    bus.publish({"type": "video", "id": vid, "reason": "saved"})


def delete_video(vid: str, keep_audio: bool = False) -> dict:
    """Remove a transcript, and by default the cached audio with it.

    The wav is the bulk of the footprint (a 108-minute broadcast is ~200MB)
    but it is also what makes a re-transcribe fast, so the caller chooses.
    """
    if not has_video(vid):
        return {"error": f"no such video '{vid}'"}
    store.delete_doc(vid)
    freed = 0
    wav = os.path.join(DATA, f"{vid}.wav")
    if not keep_audio and os.path.exists(wav):
        freed = os.path.getsize(wav)
        os.remove(wav)
    bus.publish({"type": "video", "id": vid, "reason": "deleted"})
    return {"deleted": vid, "freed_mb": round(freed / 1e6, 1)}


def job_status(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def cancel(job_id: str) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"error": "no such job"}
        if j["state"] != "running":
            return {"state": j["state"]}
        j["cancel"] = True
        snapshot = dict(j)
    store.save_job(snapshot)
    return {"state": "cancelling"}


def _cancelled(job_id: str) -> bool:
    with _lock:
        return bool(_jobs[job_id]["cancel"])


def cancel_all() -> list[str]:
    """Mark every running job cancelled. Server shutdown calls it.

    The shutdown button used to close only the live sessions and leave the jobs
    alone. With a transcription or translation thread left holding a model,
    interpreter shutdown cannot release that model, and ggml-metal's shutdown
    destructor aborted on `GGML_ASSERT([rsets->data count] == 0)` -- all eight
    crash reports on this Mac were that same stack. This only marks; it does
    not wait. The waiting is `wait_idle`'s job.
    """
    with _lock:
        ids = [jid for jid, j in _jobs.items() if j["state"] == "running"]
        for jid in ids:
            _jobs[jid]["cancel"] = True
    return ids


def running() -> list[str]:
    with _lock:
        return [jid for jid, j in _jobs.items() if j["state"] == "running"]


def _spawn(job_id: str, target, args: tuple = ()):
    """Start the job thread and remember it -- so that `wait_idle` waits on
    the thread rather than on the state."""
    t = threading.Thread(target=target, args=args, daemon=True, name=f"job-{job_id}")
    with _lock:
        _threads[job_id] = t
    t.start()


def wait_idle(timeout: float) -> bool:
    """Wait until every job thread has ended. True when they all have.

    Returning on seeing the state turn `cancelled` is too early -- `_note`
    updates memory first and writes to SQLite after, so an `os._exit` landing
    in between makes the next startup wrongly record that job as
    "interrupted because the server restarted". That save is the thread's last piece of
    work, so waiting for the thread to end means the save has finished too.

    Jobs that arrive while waiting are cancelled along with the rest. The
    server takes requests until `_stop_server` runs, so a job may have started
    right after `cancel_all`. The cancel flag is read at every chunk boundary,
    so stopping within a few seconds is the norm, and there is a cap for the
    cases that do not stop, such as a remote engine that never answers -- after
    that the caller simply ends the process.
    """
    deadline = time.time() + timeout
    while True:
        cancel_all()
        with _lock:
            for jid in [j for j, t in _threads.items() if not t.is_alive()]:
                _threads.pop(jid)
            alive = list(_threads.values())
        if not alive:
            return True
        left = deadline - time.time()
        if left <= 0:
            return False
        alive[0].join(min(left, 0.1))


def delete_backend(backend_id: str) -> dict:
    return config.delete("tr", backend_id)


def delete_asr_backend(backend_id: str) -> dict:
    return config.delete("asr", backend_id)


# ---- Translation -----------------------------------------------------------

def _context(cues: list[dict], i: int) -> list[str]:
    """The few lines just before `cues[i]`. Passed to the translator as
    reference.

    Unlike live, a VOD already has everything before and after out, so it can
    be sliced straight off by index. What comes after is not passed -- that is
    information live cannot have, and the two paths' translations would differ.
    A note is not an utterance, so it is left out.
    """
    n = mw_translate.CONTEXT_LINES
    return [c["text"] for c in cues[max(0, i - n):i]
            if (c.get("text") or "").strip() and c.get("kind") != "note"]


def _hand_translated(c: dict) -> bool:
    """A line whose translation a person matched up by hand. Once bulk
    translation overwrites it, there is no getting it back."""
    return "tr" in (c.get("edited") or "")


def _translate_rows(job_id: str, owner: str, spec: dict, meta: dict,
                    cues: list[dict], todo: list[dict], genre: str | None) -> bool:
    """Translate `todo` one line at a time and write it to the table. All
    three paths go through here.

    The return value is whether it ran to the end (True) or was cancelled
    (False). Progress is recorded in the job record, and streamed straight over
    SSE to a live window that is watching. `todo` must already have the
    hand-edited lines taken out -- that judgement is the caller's, and it
    records it as `kept`.
    """
    # The channel's glossary, when the video's channel has one. An engine that
    # uses no prompt (M2M-100) takes it nowhere; a prompt engine writes it into
    # the next line and on.
    g = store.glossary(meta.get("channel_key") or "")
    terms = (g or {}).get("terms") or []
    tr = mw_translate.build(spec, genre, terms)
    bid = spec.get("id") or config.PROTECTED["tr"]
    tgt = meta.get("viewer_lang") or "ko"
    by_id = {c["id"]: i for i, c in enumerate(cues)}
    skipped = 0

    def progress(n):
        _note(job_id, done=n, skipped=skipped,
              degraded=bool(getattr(tr, "tripped", False)),
              failures=getattr(tr, "failures", 0),
              glossary=(g["name"] if g else ""),
              glossary_terms=len(terms))

    for n, c in enumerate(todo):
        if _cancelled(job_id):
            _note(job_id, state="cancelled", done=n, skipped=skipped)
            return False
        # For the source language, what the line itself carries comes first.
        # A session started with "auto-detect" has an empty source_lang in the
        # metadata.
        src = c.get("lang") or meta.get("source_lang") or ""
        if not src or src == tgt or not tr.should_translate(c["text"], src, tgt):
            skipped += 1
        else:
            try:
                out = tr.translate(c["text"], src, tgt,
                                   _context(cues, by_id.get(c["id"], 0)))
            except Exception as exc:
                # Dropping the line because it failed makes it look as if
                # that utterance never happened. The source text is kept and
                # only the reason for the failure is recorded.
                print(f"[jobs] translation failed, keeping the source text: {exc}",
                      file=sys.stderr)
                out = c["text"]
            # A line the fallback backend produced is filed under that
            # backend's name. Recording it as if an endpoint that was never
            # reached had made it would leave no comparison to make.
            from_primary = getattr(tr, "last_used", "primary") == "primary"
            # A translation identical to the source is stored anyway. For a
            # proper noun or a short interjection, leaving it as it is is the
            # correct translation.
            if (out or "").strip():
                key = bid if from_primary else config.PROTECTED["tr"]
                store.save_translation(owner, c["id"], key, out)
                # Reaches the window that is watching right away (only a
                # live session still being received has subscribers; otherwise
                # this passes quietly).
                live.notify_translation(owner, c["id"], c.get("kind") or "final", out)
            with _lock:
                _jobs[job_id]["by_remote" if from_primary else "by_local"] += 1
        if n % 5 == 0:
            progress(n)
    progress(len(todo))
    return True


def _mark_translated(owner: str):
    d = store.doc(owner)
    if d is not None:
        d["translated"] = True
        store.save_doc(owner, d)
        # A translation has attached. Other windows with this video open
        # re-read the subtitles.
        bus.publish({"type": "video", "id": owner, "reason": "translated"})


def start_retranslate(value: str, backend_id: str, cue_ids=None,
                      genre: str | None = None) -> dict:
    """(Re)translate the subtitles. The same path for a VOD or for live.

    A `cue_ids` of None means all of them -- that is the case when the
    translation engine was switched on screen. A translation a person edited is
    not touched in either case, and is counted as `kept`.
    """
    spec = find_backend(backend_id)
    if spec is None:
        known = ", ".join(b["id"] for b in config.entries("tr"))
        return {"error": f"no such backend '{backend_id}'. Available: {known}"}
    owner = store.owner_of(value)
    meta = store.doc(owner) or store.session(owner)
    if not meta:
        return {"error": "no such video or session"}
    cues = store.cues(owner)
    if not cues:
        return {"error": "no subtitles to translate"}
    want = None if cue_ids is None else {int(i) for i in cue_ids}
    picked = [c for c in cues if want is None or c["id"] in want]
    if not picked:
        return {"error": "no subtitles selected"}

    kept = [c for c in picked if _hand_translated(c)]
    todo = [c for c in picked if not _hand_translated(c)]
    if not todo and want is not None:
        return {"error": f"all {len(picked)} selected lines are hand-edited translations"}

    # With no genre chosen, the one chosen when this video was transcribed is
    # used. This is so as not to ask again on every re-translation.
    genre = genre or meta.get("genre")
    job_id = _new_job(kind="retranslate", video=value, owner=owner,
                      backend=backend_id, genre=genre or mw_translate.DEFAULT_GENRE,
                      total=len(todo), kept=len(kept))
    if not todo:
        # Everything is hand-edited, so there is nothing to do. The job is
        # created anyway -- the screen polls that id, so there has to be
        # somewhere to answer that it is done.
        _note(job_id, state="done", elapsed=0.0)
        return {"id": job_id, "total": 0, "kept": len(kept)}

    def run():
        try:
            if _translate_rows(job_id, owner, spec, meta, cues, todo, genre):
                _mark_translated(owner)
                _note(job_id, state="done",
                      elapsed=round(time.time() - _jobs[job_id]["started"], 1))
        except (Exception, SystemExit) as exc:
            # SystemExit is caught too. It is not an Exception, and missing
            # it leaves the thread dying quietly and the job standing as
            # `running` forever.
            _note(job_id, state="error", error=str(exc)[:300])

    _spawn(job_id, run)
    return {"id": job_id, "total": len(todo), "kept": len(kept)}


def start_burn(value: str, view: str = "both") -> dict:
    """Burn the subtitles into the video file itself.

    A VOD, and a local file at that -- a YouTube or Twitch VOD is transcribed
    from an audio rendition, and the video itself is never downloaded, so
    there is no file here to burn into.
    """
    owner = store.owner_of(value)
    meta = store.doc(owner) or store.session(owner)
    if not meta:
        return {"error": "no such video"}
    media = meta.get("media_path") or ""
    if not media or not os.path.isfile(media):
        return {"error": ("burning needs a local file; a streamed VOD has no "
                          "video file on this machine")}
    if not store.cues(owner):
        return {"error": "no subtitles to burn"}
    job_id = _new_job(kind="burn", value=value, owner=owner,
                      title=meta.get("title") or "", total=100)

    def run():
        try:
            meta2, rows = export.collect(value)
            res = burn.run(media, meta2.get("duration") or 0, rows, view,
                           on_progress=lambda f: _note(job_id, done=int(f * 100)),
                           should_stop=lambda: _cancelled(job_id))
            _note(job_id, state="done", done=100, out=res["out"],
                  elapsed=round(time.time() - _jobs[job_id]["started"], 1))
        except (Exception, SystemExit) as exc:
            _note(job_id, state="error", error=str(exc)[:300])

    _spawn(job_id, run)
    return {"id": job_id}


# ---- The document view -----------------------------------------------------
#
# live.py writes the document while the talk is happening, one window at a
# time, on a cadence (OUTLINE_GAP_S) that keeps the engine free for the
# subtitles in between. A recording has nothing to wait for -- the whole
# transcript is in the table already -- and a session that has ended has no
# thread left to run a pass on, so neither of them had a document at all.
#
# This is that same work with the cadence taken out. The windows are cut the
# way `live._ol_take` cuts them and folded in with the same `outline.advance`,
# because a document written by one path has to be readable, and resumable,
# by the other -- `folded` is the bookmark they share. It runs as a job
# because one generation per window over an 80-minute recording is minutes of
# engine time, which a request thread cannot hold.


def _outline_windows(cues: list[dict],
                     budget: int) -> list[tuple[str, float, int, int]]:
    """Cut the transcript into the blocks that one pass each is handed.

    A block is (the joined speech, the media time it starts at, how many lines
    it holds, the highest cue id in it). The rule is `live._ol_take`'s: fill up
    to the budget and start a new block rather than split a line, so that the
    two paths feed the model the same shape of window.

    The highest id is taken as a maximum rather than as "the last one", because
    the lines come in time order and the number is identity, not order -- a
    line a person wrote in carries the last number while its time sits in a gap
    somewhere in the middle (store.cues).

    Notes are dropped. They are the server's own "reception was cut off here"
    lines, not speech, and summarising them puts the tool's voice into the
    speaker's document.
    """
    out: list[tuple[str, float, int, int]] = []
    parts: list[str] = []
    used, at, top, n = 0, 0.0, 0, 0
    for c in cues:
        if c.get("kind") == "note":
            continue
        text = (c.get("text") or "").strip()
        if not text:
            continue
        if parts and used + len(text) + 1 > budget:
            out.append(("\n".join(parts), at, n, top))
            parts, used, top, n = [], 0, 0, 0
        if not parts:
            at = c.get("t", 0.0)
        parts.append(text)
        used += len(text) + 1
        top = max(top, int(c["id"]))
        n += 1
    if parts:
        out.append(("\n".join(parts), at, n, top))
    return out


def _publish_outline(owner: str, doc: dict, job_id: str = ""):
    """Tell every open window that this document has grown.

    Only the counters go out, not the document. The feed reaches every
    subscriber and a document of 200 sections is not small, so this follows the
    rule the rest of `bus` already follows -- say which part changed and let
    the screen re-read that part (`/api/outline/<owner>`) if it is showing it.
    """
    bus.publish({"type": "outline", "owner": owner, "job": job_id,
                 "sections": len(doc.get("sections") or []),
                 "lines": int(doc.get("lines", 0)),
                 "folded": int(doc.get("folded", 0)),
                 "updated": doc.get("updated", 0.0),
                 "error": doc.get("error") or ""})


def start_outline(owner: str) -> dict:
    """Write the document for a recording, or for a session that has finished.

    Answers `{"job": <id>}`, or `{"error": <reason>}` with one of three
    reasons: `no-subtitles`, `session-still-live` and `backend-cannot-write`.
    They are ids rather than sentences because the screen has to tell them
    apart to say anything useful about any of them, and the last one is
    already the id `live.outline_set` refuses with -- one string for what is
    one condition, wherever it is met.
    """
    owner = store.owner_of(owner)
    session = live.get(owner)
    if session is not None:
        # Being in the live registry at all is the test, not the word in
        # `state`. A session that has just stopped stays there while
        # `_release` runs its closing pass (live._ol_flush), and a rebuild
        # started inside that window would have two threads writing one
        # `outlines` row and folding the same speech in twice. While a session
        # is live the toggle owns the document; the wait is seconds.
        return {"error": "session-still-live", "state": session.state}
    rows = [c for c in store.cues(owner)
            if c.get("kind") != "note" and (c.get("text") or "").strip()]
    if not rows:
        return {"error": "no-subtitles"}
    # The engine chosen right now, not the one the recording was transcribed
    # with. A document is written when it is asked for, and `live.outline_of`
    # already tells the screen what a rebuild would use by this same rule.
    spec = config.find_backend(config.active("tr"))
    if not mw_translate.can_write(spec):
        # Asked of the spec rather than by building the engine: `build_writer`
        # on the local backend reaches models.shared() and loads Gemma, and
        # this runs on the request thread.
        return {"error": "backend-cannot-write",
                "backend": (spec or {}).get("id") or ""}
    meta = store.doc(owner) or store.session(owner) or {}
    # A recording and a session both record the language the viewer reads
    # under this key. A record written before the setting existed has none,
    # and the configured language is the answer the live path would give.
    viewer_lang = meta.get("viewer_lang") or config.viewer_lang()
    # The channel glossary, by the same lookup `_translate_rows` makes. The
    # document puts the terms to a different use than a translation does --
    # they are the proper nouns of this talk, which are exactly the words a
    # transcript gets wrong (outline.terms_block).
    g = store.glossary(meta.get("channel_key") or "")
    terms = (g or {}).get("terms") or None

    job_id = _new_job(kind="outline", value=owner, owner=owner,
                      title=meta.get("title") or "",
                      backend=(spec or {}).get("id") or "",
                      glossary=(g["name"] if g else ""),
                      glossary_terms=len(terms or []),
                      total=len(rows), windows=0, sections=0)

    def run():
        try:
            writer = mw_translate.build_writer(spec)
            if writer is None:
                # `can_write` said yes and this says no, so the settings
                # changed between the two. The same id, so the screen has one
                # case to handle rather than two.
                _note(job_id, state="error", error="backend-cannot-write")
                return
            windows = _outline_windows(rows, mw_outline.window_chars(writer))
            _note(job_id, windows=len(windows))
            # A rebuild starts from nothing and replaces what was there. A
            # pass only ever sees the section it is writing (outline.py), so
            # carrying on from an existing document instead would fold the
            # same speech in a second time and write the talk twice.
            doc = mw_outline.empty()
            done = 0
            for window, at, n, top in windows:
                if _cancelled(job_id):
                    _note(job_id, state="cancelled", done=done,
                          sections=len(doc.get("sections") or []))
                    return
                try:
                    doc = mw_outline.advance(doc, window, viewer_lang, writer,
                                             terms, at=at, lines=n)
                except (Exception, SystemExit) as exc:
                    # A failed pass has no source text to fall back on the way
                    # a failed translation has, so the document written so far
                    # stands and the reason is said out loud -- the same rule
                    # `live._ol_pass` follows. Nothing is saved if nothing was
                    # written yet: the rebuild replaces the old document as it
                    # goes, and a first pass that failed has replaced nothing.
                    if doc.get("sections"):
                        doc["error"] = str(exc)[:200]
                        store.save_outline(owner, doc)
                        _publish_outline(owner, doc, job_id)
                    _note(job_id, state="error", error=str(exc)[:300],
                          done=done, sections=len(doc.get("sections") or []))
                    return
                doc["folded"] = max(int(doc.get("folded", 0)), top)
                done += n
                # Written every window rather than once at the end. An hour of
                # recording is tens of passes; a document that appears only
                # when the last one lands cannot be read while it is being
                # written, and a cancel halfway would leave nothing at all.
                store.save_outline(owner, doc)
                _publish_outline(owner, doc, job_id)
                _note(job_id, done=done,
                      sections=len(doc.get("sections") or []))
            _note(job_id, state="done", done=done,
                  sections=len(doc.get("sections") or []),
                  elapsed=round(time.time() - _jobs[job_id]["started"], 1))
        except (Exception, SystemExit) as exc:
            # SystemExit is caught too, for the reason the other jobs catch it:
            # missed, the thread dies quietly and the job stands as `running`
            # for ever.
            _note(job_id, state="error", error=str(exc)[:300])

    _spawn(job_id, run)
    return {"job": job_id, "total": len(rows)}


# ---- Transcription ---------------------------------------------------------

def start_transcribe(url: str, lang: str | None, viewer_lang: str,
                     backend_id: str = "", asr_id: str = "",
                     speakers: bool = False, speaker_solo: bool = False,
                     speaker_threshold: float | None = None,
                     genre: str | None = None,
                     refine: bool = True, site: str = "", channel: str = "",
                     channel_name: str = "") -> dict:
    """Take a URL from the UI all the way to a playable cue file.

    Everything the CLI does, driven from the browser, with the phase reported
    as it goes: a 108-minute broadcast spends about a minute downloading and
    twenty seconds transcribing, and a progress bar that says nothing during
    the download reads as a hang.
    """
    cfg = config.load()
    backend_id = backend_id or config.active("tr", cfg)
    asr_id = asr_id or config.active("asr", cfg)
    job_id = _new_job(kind="transcribe", url=url, phase="probe", title="",
                      video=None, asr=asr_id,
                      genre=genre or mw_translate.DEFAULT_GENRE)
    _spawn(job_id, _run_transcribe,
           (job_id, url, lang, viewer_lang, backend_id, asr_id, speakers,
            speaker_solo, speaker_threshold, genre,
            refine, site, channel, channel_name))
    return {"id": job_id}


def _run_transcribe(job_id: str, url: str, lang: str | None,
                    viewer_lang: str, backend_id: str,
                    asr_id: str = "", speakers: bool = False,
                    speaker_solo: bool = False, speaker_threshold: float | None = None,
                    genre: str | None = None, refine: bool = True,
                    site: str = "", channel: str = "",
                    channel_name: str = ""):
    def note(**kw):
        _note(job_id, **kw)

    def cancelled() -> bool:
        return _cancelled(job_id)

    try:
        meta = vod.probe(url)
        note(title=meta["title"], video=meta["id"])
        if meta["is_live"]:
            note(state="error",
                 error="this live stream is still running. The VOD flow only takes "
                       "videos that have ended.")
            return

        # A local file is not downloaded but converted. Name it honestly.
        note(phase="convert" if meta.get("source") == "file" else "download")
        os.makedirs(DATA, exist_ok=True)
        try:
            wav = vod.fetch_audio(url, os.path.join(DATA, f"{meta['id']}.wav"),
                                  should_stop=cancelled)
        except mw_stream.Cancelled:
            note(state="cancelled"); return
        if cancelled():
            note(state="cancelled"); return

        samples = vod.read_wav(wav)
        audio_s = len(samples) / vod.SAMPLE_RATE
        note(phase="transcribe", total=int(audio_s))
        engine = mw_asr.build(find_asr(asr_id))
        try:
            # This used to look at the engine name here to decide whether to
            # pass `speakers`. That test was `default`, so the speaker labels
            # never once reached the local transcriber picked in the settings
            # (`tcpp`, which is the default). Now the surface takes both, and a
            # transcriber that cannot do it ignores it (asr.ASRBackend).
            cues = engine.transcribe(samples, lang, speakers=speakers,
                                     speaker_solo=speaker_solo,
                                     speaker_threshold=speaker_threshold,
                                     refine=refine,
                                     on_progress=lambda p: note(done=int(p * audio_s)),
                                     should_stop=cancelled)
        except mw_stream.Cancelled:
            # The user stopping it is not a failure. Retrying with the
            # fallback engine would amount to ignoring the instruction to
            # stop.
            note(state="cancelled"); return
        except Exception as exc:
            if engine.name == mw_asr.DEFAULT_NAME:
                raise
            # An external ASR that refuses the job should not cost the user
            # the download; fall back so they still get a transcript.
            note(asr_fallback=str(exc)[:160])
            print(f"[job] external ASR failed ({exc}); using the local engine", flush=True)
            engine = mw_asr.DefaultLocal()
            try:
                cues = engine.transcribe(samples, lang, speakers=speakers,
                                         speaker_solo=speaker_solo,
                                         speaker_threshold=speaker_threshold,
                                         refine=refine,
                                         on_progress=lambda p: note(done=int(p * audio_s)),
                                         should_stop=cancelled)
            except mw_stream.Cancelled:
                note(state="cancelled"); return
        note(asr_used=engine.name)
        # The audio is released here. A 116-minute wav is 445MB as float32,
        # and with this name left in the function frame it stayed held all
        # through the translation stage that follows -- a few minutes for a
        # thousand lines. Nobody uses it once transcription has finished
        # (audio_s is already held as a number).
        del samples
        if cancelled():
            note(state="cancelled"); return

        counts: dict[str, int] = {}
        for c in cues:
            counts[c["lang"]] = counts.get(c["lang"], 0) + 1
        source_lang = lang or (max(counts, key=counts.get) if counts else "unknown")

        # Re-adding the same video does not throw away the translations
        # already made with another engine, nor the hand-edit marks. The
        # transcription of the same audio is the same, so a line whose text is
        # unchanged inherits what it had. (A line whose source text was itself
        # edited has text different from the new transcription and so cannot
        # inherit -- re-transcribing means asking for a fresh transcription, so
        # that is the right way round.)
        previous = []
        if has_video(meta["id"]):
            try:
                previous = load_video(meta["id"]).get("cues", [])
            except Exception:
                previous = []
        kept = 0
        merged = []
        for i, c in enumerate(cues):
            old_tr, old_edited = {}, ""
            if i < len(previous) and previous[i].get("text") == c["text"]:
                old_tr = previous[i].get("translations", {}) or {}
                old_edited = previous[i].get("edited") or ""
                if old_tr:
                    kept += 1
            merged.append({**c, "translations": dict(old_tr), "edited": old_edited})
        if kept:
            print(f"[job] kept {kept} existing translations", flush=True)

        # The channel the glossary is looked up by. The web sent the probe's
        # own answer up (site, channel), so the doc and the lookup agree even
        # when the probe shape changes under the web.
        doc = {**meta, "source_lang": source_lang, "lang_counts": counts,
               "viewer_lang": viewer_lang, "translated": bool(kept),
               "genre": genre or mw_translate.DEFAULT_GENRE,
               "audio_seconds": round(audio_s, 1), "cues": merged,
               "channel_key": store.channel_key(
                   site or meta.get("site") or "",
                   channel or meta.get("channel") or "", channel_name),
               "channel_name": (channel_name or "").strip()}
        save_video(meta["id"], doc)
        note(kept=kept)

        if source_lang == viewer_lang:
            note(phase="done", state="done", done=len(cues), total=len(cues),
                 elapsed=round(time.time() - _jobs[job_id]["started"], 1))
            return

        # Translation uses the same loop as the other two paths. Lines
        # already translated with this engine (a re-added video) and lines
        # matched up by hand are skipped.
        spec = find_backend(backend_id) or {"backend": "local"}
        bid = spec.get("id") or config.PROTECTED["tr"]
        owner = meta["id"]
        rows = store.cues(owner)
        todo = [c for c in rows
                if not c["translations"].get(bid) and not _hand_translated(c)]
        note(phase="translate", done=0, total=len(todo),
             skipped=len(rows) - len(todo))
        finished = _translate_rows(job_id, owner, spec, doc, rows, todo, genre)
        if not finished:
            return                      # the loop already recorded the cancel
        _mark_translated(owner)
        note(phase="done", state="done", done=len(todo),
             elapsed=round(time.time() - _jobs[job_id]["started"], 1))
    except (Exception, SystemExit) as exc:
        # SystemExit is caught too. It is not an Exception, and missing it
        # above leaves the thread dying quietly and the job standing as
        # `running` forever.
        note(state="error", error=str(exc)[:300])
