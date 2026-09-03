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
import config
import translate as mw_translate
import transcribe_vod as vod
import asr as mw_asr
import stream as mw_stream
import live
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
                job["error"] = "서버가 재시작되어 중단되었습니다"
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
        return {"error": f"'{vid}' 영상이 없습니다"}
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
    "서버가 재시작되어 중단되었습니다". That save is the thread's last piece of
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
    tr = mw_translate.build(spec, genre)
    bid = spec.get("id") or config.PROTECTED["tr"]
    tgt = meta.get("viewer_lang") or "ko"
    by_id = {c["id"]: i for i, c in enumerate(cues)}
    skipped = 0

    def progress(n):
        _note(job_id, done=n, skipped=skipped,
              degraded=bool(getattr(tr, "tripped", False)),
              failures=getattr(tr, "failures", 0))

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
                print(f"[jobs] 번역 실패, 원문을 남깁니다: {exc}", file=sys.stderr)
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
        return {"error": f"'{backend_id}' 백엔드가 없습니다. 사용 가능: {known}"}
    owner = store.owner_of(value)
    meta = store.doc(owner) or store.session(owner)
    if not meta:
        return {"error": "no such video or session"}
    cues = store.cues(owner)
    if not cues:
        return {"error": "번역할 자막이 없습니다"}
    want = None if cue_ids is None else {int(i) for i in cue_ids}
    picked = [c for c in cues if want is None or c["id"] in want]
    if not picked:
        return {"error": "고른 자막이 없습니다"}

    kept = [c for c in picked if _hand_translated(c)]
    todo = [c for c in picked if not _hand_translated(c)]
    if not todo and want is not None:
        return {"error": f"고른 {len(picked)}줄이 모두 손으로 고친 번역입니다"}

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


# ---- Transcription ---------------------------------------------------------

def start_transcribe(url: str, lang: str | None, viewer_lang: str,
                     backend_id: str = "", asr_id: str = "",
                     speakers: bool = False, genre: str | None = None,
                     refine: bool = True) -> dict:
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
           (job_id, url, lang, viewer_lang, backend_id, asr_id, speakers, genre,
            refine))
    return {"id": job_id}


def _run_transcribe(job_id: str, url: str, lang: str | None,
                    viewer_lang: str, backend_id: str,
                    asr_id: str = "", speakers: bool = False,
                    genre: str | None = None, refine: bool = True):
    def note(**kw):
        _note(job_id, **kw)

    def cancelled() -> bool:
        return _cancelled(job_id)

    try:
        meta = vod.probe(url)
        note(title=meta["title"], video=meta["id"])
        if meta["is_live"]:
            note(state="error",
                 error="진행 중인 라이브입니다. 녹화본 흐름은 종료된 영상만 다룹니다.")
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
            cues = engine.transcribe(samples, lang, speakers=speakers, refine=refine,
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

        doc = {**meta, "source_lang": source_lang, "lang_counts": counts,
               "viewer_lang": viewer_lang, "translated": bool(kept),
               "genre": genre or mw_translate.DEFAULT_GENRE,
               "audio_seconds": round(audio_s, 1), "cues": merged}
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
