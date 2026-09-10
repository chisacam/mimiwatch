"""Transcribing local video and audio files: recognising a path, probe, the ffmpeg conversion cache, the pipeline.

ffmpeg is never launched -- it is swapped for a fake script that writes a few
bytes to the last argument (the output file). The pipeline test fakes the heavy
stages (read_wav, the transcription engine) the same way test_jobs does.
"""
import os
import time

import numpy as np
import pytest

import asr as mw_asr
import jobs
import store
import stream
import transcribe_vod as vod
from conftest import wait_job


def test_is_local_source():
    assert vod.is_local_source("/a/b.mp4")
    assert vod.is_local_source("~/클립.mp3")
    assert vod.is_local_source("file:///a/b.mp4")
    assert vod.is_local_source("C:\\videos\\a.mp4")
    assert vod.is_local_source("D:/videos/a.mp4")
    assert not vod.is_local_source("https://youtu.be/x")
    assert not vod.is_local_source("clip.mp4")       # A relative path cannot say which file it means
    assert not vod.is_local_source("")


def test_probe_local(tmp_path):
    p = tmp_path / "인터뷰 원본.mp4"
    p.write_bytes(b"x")
    meta = vod.probe(str(p))
    assert meta["id"].startswith("file-") and meta["title"] == "인터뷰 원본"
    assert meta["source"] == "file" and meta["media_path"] == str(p)
    assert meta["is_live"] is False and meta["url"] == str(p)
    # The same path gives the same id -- the rule that a re-transcription inherits translations and hand edits depends on it.
    assert vod.probe(str(p))["id"] == meta["id"]
    # Wrapped in file:// it is still the same file.
    assert vod.probe(p.as_uri())["id"] == meta["id"]


def test_probe_local_missing_file(tmp_path):
    with pytest.raises(vod.VodError):
        vod.probe(str(tmp_path / "없음.mp4"))


def _fake_ffmpeg(tmp_path, monkeypatch):
    """A fake that writes a few bytes to the last argument and records how many times it was called in marker."""
    marker = tmp_path / "calls"
    sh = tmp_path / "ffmpeg"
    sh.write_text("#!/bin/sh\n"
                  f"echo 1 >> {marker}\n"
                  'for a; do out="$a"; done\n'
                  'printf x > "$out"\n')
    sh.chmod(0o755)
    monkeypatch.setattr(stream, "_FFMPEG", str(sh))
    return marker


def test_convert_local_caches_until_source_changes(tmp_path, monkeypatch):
    marker = _fake_ffmpeg(tmp_path, monkeypatch)
    src = tmp_path / "talk.mp3"
    src.write_bytes(b"z")
    dest = tmp_path / "out.wav"

    assert vod.fetch_audio(str(src), str(dest)) == str(dest)
    assert dest.exists() and marker.read_text().count("1") == 1

    # An unchanged source is not converted again.
    vod.fetch_audio(str(src), str(dest))
    assert marker.read_text().count("1") == 1

    # A newer source (a different file at the same path) is converted again.
    later = time.time() + 5
    os.utime(src, (later, later))
    vod.fetch_audio(str(src), str(dest))
    assert marker.read_text().count("1") == 2


class FakeEngine(mw_asr.ASRBackend):
    name = mw_asr.DEFAULT_NAME

    def __init__(self, cues):
        self._cues = cues

    def transcribe(self, samples, lang, on_progress=None, speakers=False,
                   speaker_solo=False, speaker_threshold=None,
                   should_stop=None, refine=True):
        return [dict(c) for c in self._cues]


def test_local_file_pipeline(tmp_path, monkeypatch, fake_translate):
    marker = _fake_ffmpeg(tmp_path, monkeypatch)
    src = tmp_path / "발표 녹화.mp4"
    src.write_bytes(b"z")
    cues = [{"start": 0.0, "end": 1.0, "lang": "ja", "text": "a"}]
    monkeypatch.setattr(vod, "read_wav", lambda path: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(mw_asr, "build", lambda spec: FakeEngine(cues))

    r = jobs.start_transcribe(str(src), "ja", "ko", "local-m2m100", "")
    st = wait_job(r["id"])
    assert st["state"] == "done", st
    vid = st["video"]
    assert vid.startswith("file-") and marker.exists()

    d = store.doc(vid)
    assert d["source"] == "file" and d["media_path"] == str(src)
    assert d["url"] == str(src) and d["title"] == "발표 녹화"
    # The converted wav sits where any other VOD's does -- that is why a re-transcription is fast.
    assert os.path.exists(os.path.join(jobs.DATA, f"{vid}.wav"))
    rows = store.cues(vid)
    assert rows[0]["translations"]["local-m2m100"] == "T:a"
