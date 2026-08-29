"""모델·도구 관리(modelhub). 네트워크에 나가지 않습니다 -- 내려받기는 가짜 서버
함수로 갈아 끼우고, 목록·상태·이어 받기·취소·삭제·허깅페이스 추가의 규칙만 봅니다."""
import io
import json
import os
import time

import pytest

import bus
import config
import modelhub
import paths


@pytest.fixture(autouse=True)
def model_dir(tmp_path, monkeypatch):
    mdir = tmp_path / "models"
    tdir = tmp_path / "tools"
    monkeypatch.setenv("MIMIWATCH_MODEL_DIR", str(mdir))
    monkeypatch.setattr(paths, "tools_dir", lambda: str(tdir))
    # 시험 사이에 상태가 새지 않게.
    modelhub._progress.clear()
    modelhub._cancel.clear()
    modelhub._queued.clear()
    yield mdir


class FakeResponse(io.BytesIO):
    """urllib 응답 흉내. 상태 코드와 헤더만 있으면 됩니다."""

    def __init__(self, data: bytes, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_catalog_has_the_install_script_set():
    ids = {e["id"] for e in modelhub.CATALOG}
    assert {"silero-vad", "whisper-large-v3-turbo", "gemma-4-e4b", "m2m100", "ffmpeg"} <= ids
    default = modelhub.default_ids()
    assert "gemma-4-e4b" in default and "ffmpeg" not in default      # 도구는 따로
    assert "gemma-4-e4b" not in modelhub.default_ids(with_gemma=False)


def test_status_reads_files_and_parts(model_dir):
    e = modelhub.find("silero-vad")
    assert modelhub.status(e)["state"] == "missing"
    model_dir.mkdir()
    (model_dir / "silero_vad.onnx.part").write_bytes(b"xx")
    st = modelhub.status(e)
    assert st["state"] == "partial" and st["have"] == 2
    (model_dir / "silero_vad.onnx.part").rename(model_dir / "silero_vad.onnx")
    assert modelhub.status(e)["state"] == "ready"
    ov = modelhub.overview()
    assert ov["ready"] is False            # whisper·m2m100·ffmpeg 가 아직 없습니다
    assert ov["model_dir"] == str(model_dir)


def test_tool_on_path_counts_as_present(monkeypatch):
    monkeypatch.setattr(modelhub.shutil, "which", lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
    st = modelhub.status(modelhub.find("ffmpeg"))
    assert st["state"] == "system" and st["system"] == "/usr/bin/ffmpeg"


def test_download_writes_part_then_renames_and_publishes(model_dir, monkeypatch):
    payload = b"0123456789" * 1000
    seen = []

    def fake_open(url, headers=None, timeout=60.0):
        seen.append((url, dict(headers or {})))
        return FakeResponse(payload, 200, {"Content-Length": str(len(payload))})

    monkeypatch.setattr(modelhub, "_open", fake_open)
    monkeypatch.setattr(modelhub, "PROGRESS_EVERY_S", 0.0)
    q = bus.subscribe()
    try:
        got = modelhub.download(["silero-vad"])
        assert got["queued"] == ["silero-vad"]
        assert _wait(lambda: modelhub.status(modelhub.find("silero-vad"))["state"] == "ready")
        assert (model_dir / "silero_vad.onnx").read_bytes() == payload
        assert not (model_dir / "silero_vad.onnx.part").exists()
        events = []
        while not q.empty():
            events.append(json.loads(q.get_nowait()))
        kinds = [e["state"] for e in events if e.get("type") == "model"]
        assert "downloading" in kinds and kinds[-1] == "ready"
        assert modelhub.download(["silero-vad"])["skipped"] == ["silero-vad"]   # 있으면 건너뜁니다
    finally:
        bus.unsubscribe(q)


def test_download_resumes_a_part_with_range(model_dir, monkeypatch):
    payload = b"abcdefghij" * 500
    model_dir.mkdir()
    (model_dir / "silero_vad.onnx.part").write_bytes(payload[:1200])
    asked = {}

    def fake_open(url, headers=None, timeout=60.0):
        asked.update(headers or {})
        start = int(headers["Range"].split("=")[1].rstrip("-"))
        rest = payload[start:]
        return FakeResponse(rest, 206, {"Content-Length": str(len(rest))})

    monkeypatch.setattr(modelhub, "_open", fake_open)
    modelhub.download(["silero-vad"])
    assert _wait(lambda: modelhub.status(modelhub.find("silero-vad"))["state"] == "ready")
    assert asked["Range"] == "bytes=1200-"
    assert (model_dir / "silero_vad.onnx").read_bytes() == payload


def test_download_failure_is_reported_and_retryable(model_dir, monkeypatch):
    def boom(url, headers=None, timeout=60.0):
        raise RuntimeError("network down")

    monkeypatch.setattr(modelhub, "_open", boom)
    modelhub.download(["silero-vad"])
    assert _wait(lambda: modelhub.status(modelhub.find("silero-vad"))["state"] == "error")
    st = modelhub.status(modelhub.find("silero-vad"))
    assert "network down" in st["error"]
    # 다시 받기를 청하면 지난 실패는 지워지고 다시 줄에 섭니다.
    payload = b"ok" * 10
    monkeypatch.setattr(modelhub, "_open", lambda u, h=None, timeout=60.0:
                        FakeResponse(payload, 200, {"Content-Length": str(len(payload))}))
    assert modelhub.download(["silero-vad"])["queued"] == ["silero-vad"]
    assert _wait(lambda: modelhub.status(modelhub.find("silero-vad"))["state"] == "ready")


def test_delete_removes_file_and_part(model_dir):
    model_dir.mkdir()
    (model_dir / "silero_vad.onnx").write_bytes(b"x" * 10)
    (model_dir / "silero_vad.onnx.part").write_bytes(b"y")
    got = modelhub.delete("silero-vad")
    assert got["deleted"] == "silero-vad"
    assert not (model_dir / "silero_vad.onnx").exists()
    assert not (model_dir / "silero_vad.onnx.part").exists()
    assert "error" in modelhub.delete("no-such-model")


def test_other_files_are_listed_and_deletable(model_dir):
    model_dir.mkdir()
    (model_dir / "hand-made.gguf").write_bytes(b"z" * 5)
    items = modelhub.overview()["items"]
    other = [i for i in items if i["kind"] == "other"]
    assert [o["label"] for o in other] == ["hand-made.gguf"]
    assert modelhub.delete("file:hand-made.gguf")["deleted"]
    assert not (model_dir / "hand-made.gguf").exists()


def test_add_custom_validates_then_registers_an_engine(model_dir, monkeypatch):
    assert "error" in modelhub.add_custom("tr", "bad", "x.gguf")            # 저장소 모양
    assert "error" in modelhub.add_custom("asr", "a/b", "model.bin")        # gguf 만
    assert "error" in modelhub.add_custom("zz", "a/b", "m.gguf")            # 종류

    monkeypatch.setattr(modelhub, "_hf_size", lambda repo, file, headers: 42)
    payload = b"G" * 42
    monkeypatch.setattr(modelhub, "_open", lambda u, h=None, timeout=60.0:
                        FakeResponse(payload, 200, {"Content-Length": "42"}))
    got = modelhub.add_custom("asr", "someone/whisper-small-gguf", "whisper-small-Q8_0.gguf",
                              label="Whisper small", device="cpu")
    assert got["added"] == "custom:hf-whisper-small-q8-0" and got["engine"] == "hf-whisper-small-q8-0"
    assert _wait(lambda: modelhub.status(modelhub.find(got["added"]))["state"] == "ready")
    # 받은 뒤 엔진 설정에 들어갑니다 -- 파일 이름만, 장치는 고른 대로.
    spec = config.find("asr", "hf-whisper-small-q8-0")
    assert spec and spec["backend"] == "tcpp" and spec["model"] == "whisper-small-Q8_0.gguf"
    assert spec["device"] == "cpu"
    # 같은 id 로 다시 넣을 수는 없습니다.
    assert "error" in modelhub.add_custom("asr", "someone/whisper-small-gguf",
                                          "whisper-small-Q8_0.gguf", engine_id="hf-whisper-small-q8-0")
    # 지우면 파일·목록·엔진 설정이 함께 사라집니다.
    assert modelhub.delete(got["added"])["deleted"]
    assert modelhub.find(got["added"]) is None
    assert config.find("asr", "hf-whisper-small-q8-0") is None


def test_snapshot_dir_downloads_every_file(model_dir, monkeypatch):
    files = {"model.bin": b"m" * 30, "sentencepiece.model": b"s" * 10, "README.md": b"r"}

    def fake_open(url, headers=None, timeout=60.0):
        if url.endswith("/api/models/ishiki-emo/mojicast-m2m100-ct2"):
            body = json.dumps({"siblings": [{"rfilename": n} for n in files]}).encode()
            return FakeResponse(body, 200, {"Content-Length": str(len(body))})
        name = url.rsplit("/", 1)[1]
        return FakeResponse(files[name], 200, {"Content-Length": str(len(files[name]))})

    monkeypatch.setattr(modelhub, "_open", fake_open)
    monkeypatch.setattr(modelhub, "_hf_size", lambda repo, file, headers: len(files[file]))
    modelhub.download(["m2m100"])
    assert _wait(lambda: modelhub.status(modelhub.find("m2m100"))["state"] == "ready")
    got = sorted(os.listdir(model_dir / "mojicast-m2m100-ct2"))
    assert got == ["model.bin", "sentencepiece.model"]     # README 는 받지 않습니다
