"""배경 작업: 녹화본 전사와 (재)번역.

번역은 처음에는 전사할 때 한 번 하고 자막 파일에 박아 두는 것이었습니다.
엔진을 비교하려면 이미 전사한 영상을 다시 번역할 수 있어야 하므로, 번역은
엔진 id별로 따로 들고, 작업은 배경 스레드에서 돌며 시청자는 계속 봅니다.

**번역 루프는 한 벌입니다(`_translate_rows`).** 예전에는 셋이었습니다 --
전체 번역, 골라서 재번역, 전사 직후 번역. 셋 중 하나만 손으로 고친 번역을
건너뛰었고, 다른 둘은 자막을 통째로 다시 쓰면서 `edited` 표시까지 지웠습니다.
엔진을 바꾸는 것만으로 사람이 맞춰 둔 번역이 덮이고 「원문과 다름」 표시가
사라졌습니다. 이제 셋 모두 같은 루프를 지나며, 그 루프는 한 줄씩
`store.save_translation`으로 씁니다.

설정은 `config.py`가 맡습니다. 여기 남은 `load_config` 등은 부르는 쪽을
그대로 두려는 이름뿐입니다.
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
DATA = store.DATA                 # 내려받은 wav 도 자막과 같은 곳에

# config.py로 옮긴 것들. server.py와 시험이 이 이름으로 부릅니다.
load_config = config.load
save_config = config.save
find_backend = config.find_backend
find_asr = config.find_asr
PROTECTED = config.PROTECTED

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _note(job_id: str, **kw):
    """진행 상태를 고치고 곧바로 디스크에 밀어 넣습니다.

    메모리 사본을 없애고 SQLite만 보게 할 수도 있지만, 폴링이 0.7초마다 들어오는
    경로라 조회는 메모리에서 답하는 편이 낫습니다. 쓰기는 레코드 통째로 하므로
    이 사이에 락 안에서 증가한 카운터도 함께 실려 나갑니다.
    """
    with _lock:
        j = _jobs.get(job_id)
        if j is None:
            return
        j.update(kw)
        snapshot = dict(j)
    store.save_job(snapshot)
    # 다른 창도 이 작업의 진행을 봅니다. 시작한 창은 폴링으로도 보지만, 확장이나
    # 다른 탭에서 시작한 작업은 이것이 유일한 길입니다.
    bus.publish({"type": "job", **snapshot})


def _new_job(**fields) -> str:
    """작업 레코드를 만들어 메모리와 표에 넣고 id를 돌려줍니다."""
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "running", "done": 0, "total": 0,
           "started": time.time(), "error": None, "skipped": 0, "kept": 0,
           "degraded": False, "failures": 0,
           # 줄마다 셉니다. 어느 모델이 실제로 글을 내놓았는지를 숫자로
           # 보여 주기 위해서입니다 -- "언젠가 대체가 있었다"가 아니라.
           "by_remote": 0, "by_local": 0, "cancel": False}
    job.update(fields)
    with _lock:
        _jobs[job_id] = job
        snapshot = dict(job)
    store.save_job(snapshot)
    bus.publish({"type": "job", **snapshot})
    return job_id


def restore() -> int:
    """재시작 전에 돌던 작업을 되살립니다 -- 상태만.

    작업을 굴리던 스레드는 프로세스와 함께 사라졌으므로 그 전사는 다시
    진행되지 않습니다. `running`인 채로 두면 UI가 끝나지 않을 폴링을 계속하게
    되니, 중단되었다고 정직하게 적습니다. 어디까지 갔었는지는 그대로 남습니다.
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


# 녹화본도 라이브와 같은 표에 담깁니다. 예전에는 `data/<영상id>.json` 파일
# 하나였는데, 그러면 자막 한 줄을 고칠 때마다 그 영상의 자막을 통째로 다시
# 써야 합니다. 83분짜리가 수백 줄이고, 쓰는 도중에 죽으면 전부 잃습니다.
# 아래 셋은 부르는 쪽을 그대로 두려고 이름을 남긴 껍데기입니다.

def has_video(vid: str) -> bool:
    return store.doc(vid) is not None


def load_video(vid: str) -> dict:
    meta = store.doc(vid)
    if meta is None:
        raise KeyError(vid)
    # id 를 함께 냅니다. 자막 한 줄을 고치려면 화면이 그 줄을 가리킬 수
    # 있어야 하는데, 위치(index)는 한 줄만 지워도 어긋납니다.
    cues = [{"id": c["id"], "start": c["t"], "end": c["end"], "lang": c["lang"],
             "text": c["text"], "translations": c["translations"],
             "edited": c["edited"]}
            | ({"speaker": c["speaker"]} if c["speaker"] else {})
            for c in store.cues(vid)]
    doc = {**meta, "cues": cues}
    doc["backends_done"] = sorted({b for c in cues for b in c["translations"]})
    return doc


def save_video(vid: str, doc: dict):
    """전사 결과를 통째로 씁니다. **전사가 끝났을 때만** 부릅니다 -- 자막을
    전부 갈아 끼우므로, 번역이나 편집처럼 몇 줄만 바뀌는 일에는 쓰지
    않습니다. 그쪽은 `store.save_translation`/`store.edit_cue`입니다."""
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


def delete_backend(backend_id: str) -> dict:
    return config.delete("tr", backend_id)


def delete_asr_backend(backend_id: str) -> dict:
    return config.delete("asr", backend_id)


# ---- 번역 -------------------------------------------------------------------

def _context(cues: list[dict], i: int) -> list[str]:
    """`cues[i]` 직전 몇 줄. 번역기에 참고로 넘깁니다.

    녹화본은 라이브와 달리 앞뒤가 전부 이미 나와 있으므로 인덱스로 바로
    잘라내면 됩니다. 뒤쪽은 넘기지 않습니다 -- 라이브에서는 있을 수 없는
    정보라 두 경로의 번역이 달라집니다. 안내(note)는 발화가 아니므로 뺍니다.
    """
    n = mw_translate.CONTEXT_LINES
    return [c["text"] for c in cues[max(0, i - n):i]
            if (c.get("text") or "").strip() and c.get("kind") != "note"]


def _hand_translated(c: dict) -> bool:
    """사람이 번역을 손으로 맞춘 줄. 뭉텅이 번역이 덮으면 되돌릴 수 없습니다."""
    return "tr" in (c.get("edited") or "")


def _translate_rows(job_id: str, owner: str, spec: dict, meta: dict,
                    cues: list[dict], todo: list[dict], genre: str | None) -> bool:
    """`todo`를 한 줄씩 번역해 표에 씁니다. 세 경로가 전부 여기를 지납니다.

    돌려주는 값은 끝까지 갔는지(True)/취소되었는지(False)입니다. 진행률은
    작업 레코드에 적고, 보고 있는 라이브 창에는 SSE로 곧바로 흘려보냅니다.
    `todo`는 이미 손편집 줄을 뺀 것이어야 합니다 -- 그 판단은 부르는 쪽이
    하고 `kept`로 적습니다.
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
        # 원본 언어는 그 줄이 들고 있는 것을 먼저 씁니다. 「자동 판별」로
        # 켠 세션은 메타의 source_lang 이 비어 있습니다.
        src = c.get("lang") or meta.get("source_lang") or ""
        if not src or src == tgt or not tr.should_translate(c["text"], src, tgt):
            skipped += 1
        else:
            try:
                out = tr.translate(c["text"], src, tgt,
                                   _context(cues, by_id.get(c["id"], 0)))
            except Exception as exc:
                # 실패했다고 줄을 버리면 그 발화가 없었던 것처럼 보입니다.
                # 원문을 남기고 왜 실패했는지만 적습니다.
                print(f"[jobs] 번역 실패, 원문을 남깁니다: {exc}", file=sys.stderr)
                out = c["text"]
            # 대체 백엔드가 낸 줄은 그 백엔드 이름으로 남깁니다. 닿지 않은
            # 엔드포인트가 만든 것처럼 기록하면 비교가 성립하지 않습니다.
            from_primary = getattr(tr, "last_used", "primary") == "primary"
            # 번역이 원문과 같아도 저장합니다. 고유명사나 짧은 감탄사는
            # 그대로 두는 것이 옳은 번역입니다.
            if (out or "").strip():
                key = bid if from_primary else config.PROTECTED["tr"]
                store.save_translation(owner, c["id"], key, out)
                # 보고 있는 창에도 바로 닿게 합니다(받는 중인 라이브만 구독자가
                # 있습니다. 아니면 조용히 지나갑니다).
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
        # 번역이 붙었습니다. 이 영상을 열어 둔 다른 창은 자막을 다시 읽습니다.
        bus.publish({"type": "video", "id": owner, "reason": "translated"})


def start_retranslate(value: str, backend_id: str, cue_ids=None,
                      genre: str | None = None) -> dict:
    """자막을 (다시) 번역합니다. 녹화본이든 라이브든 같은 길입니다.

    `cue_ids`가 None이면 전부입니다 -- 화면에서 번역 엔진을 바꾸었을 때가
    이 경우입니다. 사람이 고친 번역은 어느 경우에도 건드리지 않고 `kept`로
    셉니다.
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

    # 장르를 고르지 않았다면 이 영상을 전사할 때 골랐던 것을 씁니다. 다시
    # 번역할 때마다 되묻지 않기 위해서입니다.
    genre = genre or meta.get("genre")
    job_id = _new_job(kind="retranslate", video=value, owner=owner,
                      backend=backend_id, genre=genre or mw_translate.DEFAULT_GENRE,
                      total=len(todo), kept=len(kept))
    if not todo:
        # 전부 손편집이라 할 일이 없습니다. 작업은 만들어 둡니다 -- 화면이
        # 그 id를 폴링하므로 끝났다고 답할 자리가 있어야 합니다.
        _note(job_id, state="done", elapsed=0.0)
        return {"id": job_id, "total": 0, "kept": len(kept)}

    def run():
        try:
            if _translate_rows(job_id, owner, spec, meta, cues, todo, genre):
                _mark_translated(owner)
                _note(job_id, state="done",
                      elapsed=round(time.time() - _jobs[job_id]["started"], 1))
        except (Exception, SystemExit) as exc:
            # SystemExit도 받습니다. Exception이 아니라 놓치면 스레드가 조용히
            # 죽고 작업은 `running`으로 영원히 남습니다.
            _note(job_id, state="error", error=str(exc)[:300])

    threading.Thread(target=run, daemon=True).start()
    return {"id": job_id, "total": len(todo), "kept": len(kept)}


# ---- 전사 -------------------------------------------------------------------

def start_transcribe(url: str, lang: str | None, viewer_lang: str,
                     backend_id: str = "", asr_id: str = "",
                     speakers: bool = False, genre: str | None = None) -> dict:
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
    threading.Thread(target=_run_transcribe,
                     args=(job_id, url, lang, viewer_lang, backend_id, asr_id,
                           speakers, genre),
                     daemon=True).start()
    return {"id": job_id}


def _run_transcribe(job_id: str, url: str, lang: str | None,
                    viewer_lang: str, backend_id: str,
                    asr_id: str = "", speakers: bool = False,
                    genre: str | None = None):
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

        note(phase="download")
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
            kw = {"speakers": speakers} if engine.name == mw_asr.DEFAULT_NAME else {}
            cues = engine.transcribe(samples, lang, **kw,
                                     on_progress=lambda p: note(done=int(p * audio_s)),
                                     should_stop=cancelled)
        except mw_stream.Cancelled:
            # 사용자가 멈춘 것은 실패가 아닙니다. 대체 엔진으로 다시
            # 시도하면 멈추라는 말을 무시하는 셈이 됩니다.
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
                cues = engine.transcribe(samples, lang,
                                         on_progress=lambda p: note(done=int(p * audio_s)),
                                         should_stop=cancelled)
            except mw_stream.Cancelled:
                note(state="cancelled"); return
        note(asr_used=engine.name)
        if cancelled():
            note(state="cancelled"); return

        counts: dict[str, int] = {}
        for c in cues:
            counts[c["lang"]] = counts.get(c["lang"], 0) + 1
        source_lang = lang or (max(counts, key=counts.get) if counts else "unknown")

        # 같은 영상을 다시 넣어도 다른 엔진으로 이미 만든 번역과 사람이
        # 손댄 표시를 버리지 않습니다. 같은 오디오의 전사는 같으므로, 글자가
        # 그대로인 줄은 가진 것을 그대로 물려받습니다. (원문 자체를 고쳤던
        # 줄은 새 전사와 글자가 다르므로 물려받지 못합니다 -- 다시 전사한다는
        # 것은 전사를 새로 받겠다는 뜻이니 그 편이 맞습니다.)
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

        # 번역은 다른 두 경로와 같은 루프를 씁니다. 이 엔진으로 이미 번역된
        # 줄(다시 넣은 영상)과 손으로 맞춘 줄은 건너뜁니다.
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
            return                      # 취소는 루프가 이미 적었습니다
        _mark_translated(owner)
        note(phase="done", state="done", done=len(todo),
             elapsed=round(time.time() - _jobs[job_id]["started"], 1))
    except (Exception, SystemExit) as exc:
        # SystemExit도 받습니다. Exception이 아니라 위에서 놓치면 스레드가
        # 조용히 죽고 작업은 `running`으로 영원히 남습니다.
        note(state="error", error=str(exc)[:300])
