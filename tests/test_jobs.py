"""배경 작업: 번역 루프 한 벌이 손편집을 지키는가, 실패가 작업을 멈춰 세우지 않는가."""
import numpy as np

import asr as mw_asr
import jobs
import store
import transcribe_vod as vod
from conftest import wait_job

VID = "vid1"


def _seed_video():
    store.save_doc(VID, {"id": VID, "title": "t", "source_lang": "ja", "viewer_lang": "ko",
                         "genre": "general"})
    store.replace_cues(VID, [
        {"start": 0, "end": 1, "text": "a", "lang": "ja", "translations": {}},
        {"start": 2, "end": 3, "text": "b", "lang": "ja",
         "translations": {"local-m2m100": "사람번역"}, "edited": "tr"},
        {"start": 4, "end": 5, "text": "c", "lang": "ja", "translations": {}, "edited": "text"},
    ])


def test_full_translate_keeps_hand_edits(fake_translate):
    _seed_video()
    r = jobs.start_retranslate(VID, "local-m2m100", None, None)     # /api/translate 경로
    st = wait_job(r["id"])
    assert st["state"] == "done" and st["kept"] == 1 and st["done"] == 2
    rows = store.cues(VID)
    assert rows[0]["translations"]["local-m2m100"] == "T:a"
    assert rows[1]["translations"]["local-m2m100"] == "사람번역" and "tr" in rows[1]["edited"]
    assert rows[2]["translations"]["local-m2m100"] == "T:c" and "text" not in rows[2]["edited"]
    assert store.doc(VID)["translated"] is True


def test_translate_context_is_previous_lines_only(fake_translate):
    _seed_video()
    r = jobs.start_retranslate(VID, "local-m2m100", [3], None)
    wait_job(r["id"])
    (tr,) = fake_translate
    assert tr.calls == [("c", ["a", "b"])]


def test_all_hand_edited_full_translate_is_done_job(fake_translate):
    store.save_doc(VID, {"id": VID, "source_lang": "ja", "viewer_lang": "ko"})
    store.replace_cues(VID, [{"start": 0, "end": 1, "text": "a", "lang": "ja",
                              "translations": {"g": "x"}, "edited": "tr"}])
    r = jobs.start_retranslate(VID, "local-m2m100", None, None)
    assert r["kept"] == 1 and r["total"] == 0
    assert jobs.job_status(r["id"])["state"] == "done"
    # 골라서 돌린 것이면 할 일이 없다고 말합니다.
    assert "error" in jobs.start_retranslate(VID, "local-m2m100", [1], None)


def test_system_exit_in_worker_becomes_job_error(monkeypatch):
    _seed_video()
    import translate

    def boom(spec, genre=None):
        raise SystemExit("yt-dlp failed")
    monkeypatch.setattr(translate, "build", boom)
    r = jobs.start_retranslate(VID, "local-m2m100", None, None)
    st = wait_job(r["id"])
    assert st["state"] == "error" and "yt-dlp" in st["error"]


def test_cancel_stops_translation(monkeypatch):
    import time

    import translate
    from conftest import FakeTranslator
    store.save_doc(VID, {"id": VID, "source_lang": "ja", "viewer_lang": "ko"})
    store.replace_cues(VID, [{"start": i, "end": i + 1, "text": f"t{i}", "lang": "ja",
                              "translations": {}} for i in range(50)])

    def slow(text, s, g, c):
        time.sleep(0.01)
        return "T:" + text
    monkeypatch.setattr(translate, "build", lambda spec, genre=None: FakeTranslator(slow))
    r = jobs.start_retranslate(VID, "local-m2m100", None, None)
    jobs.cancel(r["id"])
    st = wait_job(r["id"])
    assert st["state"] == "cancelled"
    assert sum(1 for c in store.cues(VID) if c["translations"]) < 50


class FakeEngine(mw_asr.ASRBackend):
    name = mw_asr.DEFAULT_NAME

    def __init__(self, cues):
        self._cues = cues

    def transcribe(self, samples, lang, on_progress=None, speakers=False, should_stop=None):
        return [dict(c) for c in self._cues]


def test_retranscribe_keeps_translations_and_edits_for_same_text(monkeypatch, fake_translate):
    cues = [{"start": 0.0, "end": 1.0, "lang": "ja", "text": "a"},
            {"start": 1.0, "end": 2.0, "lang": "ja", "text": "b"}]
    monkeypatch.setattr(vod, "probe", lambda url: {"id": VID, "title": "t", "duration": 2,
                                                   "uploader": "u", "is_live": False, "url": url})
    monkeypatch.setattr(vod, "fetch_audio", lambda url, dest, should_stop=None: dest)
    monkeypatch.setattr(vod, "read_wav", lambda path: np.zeros(16000 * 2, dtype=np.float32))
    monkeypatch.setattr(mw_asr, "build", lambda spec: FakeEngine(cues))

    r = jobs.start_transcribe("https://x", "ja", "ko", "local-m2m100", "")
    st = wait_job(r["id"])
    assert st["state"] == "done", st
    rows = store.cues(VID)
    assert [c["translations"]["local-m2m100"] for c in rows] == ["T:a", "T:b"]

    # 사람이 두 번째 줄의 번역을 맞춰 두었습니다. 같은 영상을 다시 넣어도 남아야 합니다.
    store.edit_cue(VID, 2, tr="사람", backend="local-m2m100")
    r = jobs.start_transcribe("https://x", "ja", "ko", "local-m2m100", "")
    st = wait_job(r["id"])
    assert st["state"] == "done", st
    rows = store.cues(VID)
    assert rows[1]["translations"]["local-m2m100"] == "사람" and "tr" in rows[1]["edited"]
    assert rows[0]["translations"]["local-m2m100"] == "T:a"
    assert st["kept"] == 2                       # 물려받은 번역 2줄
    # 이 엔진으로 이미 번역된 줄은 다시 돌리지 않았습니다.
    assert st["total"] == 0


def test_fetch_audio_cancel_kills_download(monkeypatch, tmp_path):
    import subprocess
    import stream
    killed = []

    class P:
        returncode = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired("yt-dlp", timeout)

        def kill(self):
            killed.append(1)

        def wait(self):
            pass

    monkeypatch.setattr(vod.subprocess, "Popen", lambda *a, **k: P())
    try:
        vod.fetch_audio("u", str(tmp_path / "a.wav"), should_stop=lambda: True)
    except stream.Cancelled:
        pass
    else:
        raise AssertionError("Cancelled가 나야 합니다")
    assert killed


def test_probe_failure_is_not_system_exit(monkeypatch):
    import types
    monkeypatch.setattr(vod.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    try:
        vod.probe("x")
    except vod.VodError as exc:
        assert "boom" in str(exc)
        assert isinstance(exc, Exception) and not isinstance(exc, SystemExit)
    else:
        raise AssertionError("VodError가 나야 합니다")
