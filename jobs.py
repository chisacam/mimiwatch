"""Background re-translation jobs.

Translation used to happen once, at transcription time, and got baked into
the cue file. Comparing backends means being able to re-translate a video
that is already transcribed, so translations are keyed by backend id and a
job runs in the background while the viewer keeps watching.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

import translate as mw_translate
import transcribe_vod as vod
import asr as mw_asr

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
CONFIG = os.path.join(BASE, "backends.json")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


EXAMPLE_CONFIG = os.path.join(BASE, "backends.example.json")


def load_config() -> dict:
    # backends.json holds real endpoints and keys and is not in the repo;
    # first run seeds it from the example so the app starts out of the box.
    if not os.path.exists(CONFIG) and os.path.exists(EXAMPLE_CONFIG):
        import shutil
        shutil.copy(EXAMPLE_CONFIG, CONFIG)
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def find_backend(backend_id: str) -> dict | None:
    for b in load_config()["backends"]:
        if b["id"] == backend_id:
            return b
    return None


def find_asr(backend_id: str) -> dict | None:
    for b in load_config().get("asr_backends", []):
        if b["id"] == backend_id:
            return b
    return None


def video_path(vid: str) -> str:
    return os.path.join(DATA, f"{vid}.json")


def load_video(vid: str) -> dict:
    with open(video_path(vid), encoding="utf-8") as f:
        doc = json.load(f)
    return migrate(doc)


def migrate(doc: dict) -> dict:
    """Older files carry a single `translation` per cue; fold it into the
    per-backend map so both shapes can coexist without a data migration
    step the user has to run."""
    for c in doc.get("cues", []):
        if "translations" not in c:
            c["translations"] = ({"local-m2m100": c["translation"]}
                                 if c.get("translation") else {})
    doc["backends_done"] = sorted({b for c in doc.get("cues", [])
                                   for b in c["translations"]})
    return doc


def save_video(vid: str, doc: dict):
    # Recompute rather than trust the value loaded before this pass ran; a
    # job that just added a backend would otherwise write a stale list.
    doc["backends_done"] = sorted({b for c in doc.get("cues", [])
                                   for b in c.get("translations", {})})
    tmp = video_path(vid) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, video_path(vid))


def delete_video(vid: str, keep_audio: bool = False) -> dict:
    """Remove a transcript, and by default the cached audio with it.

    The wav is the bulk of the footprint (a 108-minute broadcast is ~200MB)
    but it is also what makes a re-transcribe fast, so the caller chooses.
    """
    path = video_path(vid)
    if not os.path.exists(path):
        return {"error": f"'{vid}' 영상이 없습니다"}
    os.remove(path)
    freed = 0
    wav = os.path.join(DATA, f"{vid}.wav")
    if not keep_audio and os.path.exists(wav):
        freed = os.path.getsize(wav)
        os.remove(wav)
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
    return {"state": "cancelling"}


def delete_backend(backend_id: str) -> dict:
    cfg = load_config()
    if backend_id == "local-m2m100":
        return {"error": "기본 로컬 백엔드는 삭제할 수 없습니다"}
    before = len(cfg["backends"])
    cfg["backends"] = [b for b in cfg["backends"] if b["id"] != backend_id]
    if len(cfg["backends"]) == before:
        return {"error": f"'{backend_id}' 백엔드가 없습니다"}
    if cfg.get("active") == backend_id:
        cfg["active"] = "local-m2m100"
    save_config(cfg)
    return cfg


def start(vid: str, backend_id: str) -> dict:
    spec = find_backend(backend_id)
    if spec is None:
        known = ", ".join(b["id"] for b in load_config()["backends"])
        return {"error": f"'{backend_id}' 백엔드가 없습니다. 사용 가능: {known}"}
    doc = load_video(vid)
    if doc["source_lang"] == doc.get("viewer_lang"):
        return {"error": "source language matches the viewer language"}

    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"id": job_id, "video": vid, "backend": backend_id,
                         "done": 0, "total": len(doc["cues"]), "state": "running",
                         "started": time.time(), "error": None, "skipped": 0,
                         "degraded": False, "failures": 0,
                         # Counted per line so the viewer can see which model
                         # is actually producing text, not just that a
                         # fallback happened at some point.
                         "by_remote": 0, "by_local": 0, "cancel": False}

    threading.Thread(target=_run, args=(job_id, vid, spec, doc), daemon=True).start()
    return {"id": job_id}


def start_transcribe(url: str, lang: str | None, viewer_lang: str,
                     backend_id: str = "local-m2m100",
                     asr_id: str = "local-hayamimi",
                     speakers: bool = False) -> dict:
    """Take a URL from the UI all the way to a playable cue file.

    Everything the CLI does, driven from the browser, with the phase reported
    as it goes: a 108-minute broadcast spends about a minute downloading and
    twenty seconds transcribing, and a progress bar that says nothing during
    the download reads as a hang.
    """
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"id": job_id, "kind": "transcribe", "url": url,
                         "phase": "probe", "state": "running", "done": 0, "total": 0,
                         "started": time.time(), "error": None, "cancel": False,
                         "title": "", "video": None, "skipped": 0,
                         "by_remote": 0, "by_local": 0, "degraded": False,
                         "failures": 0, "asr": asr_id}
    threading.Thread(target=_run_transcribe,
                     args=(job_id, url, lang, viewer_lang, backend_id, asr_id,
                           speakers),
                     daemon=True).start()
    return {"id": job_id}


def _run_transcribe(job_id: str, url: str, lang: str | None,
                    viewer_lang: str, backend_id: str,
                    asr_id: str = "local-hayamimi", speakers: bool = False):
    def note(**kw):
        with _lock:
            _jobs[job_id].update(kw)

    def cancelled() -> bool:
        with _lock:
            return _jobs[job_id]["cancel"]

    try:
        meta = vod.probe(url)
        note(title=meta["title"], video=meta["id"])
        if meta["is_live"]:
            note(state="error",
                 error="진행 중인 라이브입니다. 녹화본 흐름은 종료된 영상만 다룹니다.")
            return

        note(phase="download")
        os.makedirs(DATA, exist_ok=True)
        wav = vod.fetch_audio(url, os.path.join(DATA, f"{meta['id']}.wav"))
        if cancelled():
            note(state="cancelled"); return

        samples = vod.read_wav(wav)
        audio_s = len(samples) / vod.SAMPLE_RATE
        note(phase="transcribe", total=int(audio_s))
        engine = mw_asr.build(find_asr(asr_id))
        try:
            kw = {"speakers": speakers} if engine.name == "local-hayamimi" else {}
            cues = engine.transcribe(samples, lang, **kw,
                                     on_progress=lambda p: note(done=int(p * audio_s)))
        except Exception as exc:
            if engine.name == "local-hayamimi":
                raise
            # An external ASR that refuses the job should not cost the user
            # the download; fall back so they still get a transcript.
            note(asr_fallback=str(exc)[:160])
            print(f"[job] external ASR failed ({exc}); using hayamimi", flush=True)
            engine = mw_asr.LocalHayamimi()
            cues = engine.transcribe(samples, lang,
                                     on_progress=lambda p: note(done=int(p * audio_s)))
        note(asr_used=engine.name)
        if cancelled():
            note(state="cancelled"); return

        counts: dict[str, int] = {}
        for c in cues:
            counts[c["lang"]] = counts.get(c["lang"], 0) + 1
        source_lang = lang or (max(counts, key=counts.get) if counts else "unknown")

        # Re-adding a video must not throw away translations already paid
        # for with another backend. Transcription is deterministic for the
        # same audio, so a cue whose text is unchanged keeps what it had.
        previous = []
        if os.path.exists(video_path(meta["id"])):
            try:
                previous = load_video(meta["id"]).get("cues", [])
            except Exception:
                previous = []
        kept = 0
        merged = []
        for i, c in enumerate(cues):
            old_tr = {}
            if i < len(previous) and previous[i].get("text") == c["text"]:
                old_tr = previous[i].get("translations", {}) or {}
                if old_tr:
                    kept += 1
            merged.append({**c, "translations": dict(old_tr)})
        if kept:
            print(f"[job] kept {kept} existing translations", flush=True)

        doc = {**meta, "source_lang": source_lang, "lang_counts": counts,
               "viewer_lang": viewer_lang, "translated": bool(kept),
               "audio_seconds": round(audio_s, 1), "cues": merged}
        save_video(meta["id"], doc)
        note(kept=kept)
        note(phase="translate", done=0, total=len(cues))

        if source_lang == viewer_lang:
            note(phase="done", state="done",
                 elapsed=round(time.time() - _jobs[job_id]["started"], 1))
            return

        spec = find_backend(backend_id) or {"backend": "local"}
        tr = mw_translate.build(spec)
        bid = spec.get("id", "local-m2m100")
        skipped = 0
        for i, c in enumerate(doc["cues"]):
            if cancelled():
                save_video(meta["id"], doc)
                note(state="cancelled"); return
            if c["translations"].get(bid):
                skipped += 1          # already done by this backend
            elif not tr.should_translate(c["text"], source_lang, viewer_lang):
                skipped += 1
            else:
                out = tr.translate(c["text"], source_lang, viewer_lang)
                from_primary = getattr(tr, "last_used", "primary") == "primary"
                if out and out.strip() != c["text"].strip():
                    c["translations"][bid if from_primary else "local-m2m100"] = out
                with _lock:
                    _jobs[job_id]["by_remote" if from_primary else "by_local"] += 1
            if i % 10 == 0:
                note(done=i, skipped=skipped,
                     degraded=bool(getattr(tr, "tripped", False)),
                     failures=getattr(tr, "failures", 0))
        doc["translated"] = True
        save_video(meta["id"], doc)
        note(phase="done", state="done", done=len(doc["cues"]), skipped=skipped,
             elapsed=round(time.time() - _jobs[job_id]["started"], 1))
    except Exception as exc:
        note(state="error", error=str(exc)[:300])


def _run(job_id: str, vid: str, spec: dict, doc: dict):
    def note(**kw):
        with _lock:
            _jobs[job_id].update(kw)

    try:
        tr = mw_translate.build(spec)
        src, tgt = doc["source_lang"], doc["viewer_lang"]
        bid = spec["id"]
        skipped = 0
        for i, c in enumerate(doc["cues"]):
            with _lock:
                if _jobs[job_id]["cancel"]:
                    note(state="cancelled", done=i)
                    save_video(vid, doc)   # keep whatever was finished
                    return
            if not tr.should_translate(c["text"], src, tgt):
                skipped += 1
            else:
                out = tr.translate(c["text"], src, tgt)
                # Only record the result under this backend when this backend
                # actually produced it; a fallback line belongs to the local
                # model, not to the endpoint that was unreachable.
                from_primary = getattr(tr, "last_used", "primary") == "primary"
                if out and out.strip() != c["text"].strip():
                    c["translations"][bid if from_primary else "local-m2m100"] = out
                with _lock:
                    key = "by_remote" if from_primary else "by_local"
                    _jobs[job_id][key] += 1
            if i % 10 == 0:
                note(done=i, skipped=skipped,
                     degraded=bool(getattr(tr, "tripped", False)),
                     failures=getattr(tr, "failures", 0))
        note(done=len(doc["cues"]), skipped=skipped,
             degraded=bool(getattr(tr, "tripped", False)),
             failures=getattr(tr, "failures", 0))
        doc["translated"] = True
        save_video(vid, doc)
        note(state="done", elapsed=round(time.time() - _jobs[job_id]["started"], 1))
    except Exception as exc:  # a failed job must not take the server with it
        note(state="error", error=str(exc)[:300])
