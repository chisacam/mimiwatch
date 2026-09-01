"""로컬 영상·음성 파일 전사: 경로 판별, probe, ffmpeg 변환 캐시, 파이프라인.

ffmpeg 는 올리지 않습니다 -- 마지막 인자(출력 파일)에 몇 바이트를 쓰는 가짜
스크립트로 바꿉니다. 파이프라인 시험은 test_jobs 와 같은 방식으로 무거운
단계(read_wav, 전사 엔진)를 가짜로 둡니다.
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
    assert not vod.is_local_source("clip.mp4")       # 상대 경로는 어느 파일인지 말할 수 없습니다
    assert not vod.is_local_source("")


def test_probe_local(tmp_path):
    p = tmp_path / "인터뷰 원본.mp4"
    p.write_bytes(b"x")
    meta = vod.probe(str(p))
    assert meta["id"].startswith("file-") and meta["title"] == "인터뷰 원본"
    assert meta["source"] == "file" and meta["media_path"] == str(p)
    assert meta["is_live"] is False and meta["url"] == str(p)
    # 같은 경로는 같은 id -- 재전사가 번역·손편집을 물려받는 규칙이 성립해야 합니다.
    assert vod.probe(str(p))["id"] == meta["id"]
    # file:// 로 싸도 같은 파일입니다.
    assert vod.probe(p.as_uri())["id"] == meta["id"]


def test_probe_local_missing_file(tmp_path):
    with pytest.raises(vod.VodError):
        vod.probe(str(tmp_path / "없음.mp4"))


def _fake_ffmpeg(tmp_path, monkeypatch):
    """마지막 인자에 몇 바이트를 쓰고, 몇 번 불렸는지 marker 에 남기는 가짜."""
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

    # 원본이 그대로면 다시 변환하지 않습니다.
    vod.fetch_audio(str(src), str(dest))
    assert marker.read_text().count("1") == 1

    # 원본이 새로워지면(같은 경로에 다른 파일) 다시 변환합니다.
    later = time.time() + 5
    os.utime(src, (later, later))
    vod.fetch_audio(str(src), str(dest))
    assert marker.read_text().count("1") == 2


class FakeEngine(mw_asr.ASRBackend):
    name = mw_asr.DEFAULT_NAME

    def __init__(self, cues):
        self._cues = cues

    def transcribe(self, samples, lang, on_progress=None, speakers=False, should_stop=None):
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
    # 변환된 wav 는 여느 녹화본과 같은 자리에 있습니다 -- 재전사가 빠른 이유입니다.
    assert os.path.exists(os.path.join(jobs.DATA, f"{vid}.wav"))
    rows = store.cues(vid)
    assert rows[0]["translations"]["local-m2m100"] == "T:a"
