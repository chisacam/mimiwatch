"""Model and tool management (modelhub). Nothing goes to the network -- the download
is swapped for a fake server function, and only the rules are examined: listing,
status, resume, cancel, delete and adding from Hugging Face."""
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
    # Keep state from leaking between tests.
    modelhub._progress.clear()
    modelhub._cancel.clear()
    modelhub._queued.clear()
    yield mdir


class FakeResponse(io.BytesIO):
    """Stand-in for a urllib response. A status code and headers are all it needs."""

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


def test_catalog_and_defaults_follow_the_light_config():
    ids = {e["id"] for e in modelhub.CATALOG}
    assert {"silero-vad", "whisper-large-v3-turbo", "gemma-4-e4b", "m2m100", "ffmpeg"} <= ids
    # The example config defaults to the light CPU engines. Both the required and the default set follow that.
    need = modelhub.required_ids()
    assert need == ["silero-vad", "sensevoice-small", "m2m100", "ffmpeg"]
    default = modelhub.default_ids()
    assert "sensevoice-small" in default and "m2m100" in default
    assert "gemma-4-e4b" not in default and "whisper-large-v3-turbo" not in default
    assert "ffmpeg" not in default                                      # Tools are counted separately
    assert "gemma-4-e4b" in modelhub.default_ids(with_gemma=True)


def test_required_follows_the_active_engines(monkeypatch):
    config.set_active("asr", "tcpp-best")
    config.set_active("tr", "local-gemma")
    need = modelhub.required_ids()
    assert "whisper-large-v3-turbo" in need and "gemma-4-e4b" in need
    assert "sensevoice-small" not in need and "m2m100" in need          # The fallback path is always needed
    ov = modelhub.overview()
    flagged = {i["id"] for i in ov["items"] if i.get("required")}
    assert flagged == set(need)
    assert ov["asr_active"] == "tcpp-best" and ov["setup_done"] is False


def test_setup_options_and_apply(monkeypatch):
    opts = modelhub.setup_options()
    asr = {o["id"]: o for o in opts["asr"]}
    assert asr["tcpp-lite"]["model"]["id"] == "sensevoice-small"
    assert asr["tcpp-best"]["model"]["id"] == "whisper-large-v3-turbo"
    tr = {o["id"]: o for o in opts["tr"]}
    assert tr["local-gemma"]["model"]["id"] == "gemma-4-e4b"
    assert tr["local-m2m100"]["model"]["id"] == "m2m100"
    assert tr["gemma4-e4b"]["model"] is None                            # A remote engine has nothing to download

    queued = []
    monkeypatch.setattr(modelhub, "download", lambda ids, token=None: (queued.extend(ids), {"queued": ids})[1])
    got = modelhub.apply_setup("tcpp-best", "local-gemma")
    assert got["ok"] and config.active("asr") == "tcpp-best" and config.active("tr") == "local-gemma"
    assert config.setup_done() is True
    assert "whisper-large-v3-turbo" in queued and "gemma-4-e4b" in queued and "m2m100" in queued
    assert "error" in modelhub.apply_setup("no-such", "local-gemma")


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
    assert ov["ready"] is False            # whisper, m2m100 and ffmpeg are still missing
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
        assert modelhub.download(["silero-vad"])["skipped"] == ["silero-vad"]   # Already there, so it is skipped
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
    # Asking for the download again clears the past failure and puts it back in the queue.
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
    assert "error" in modelhub.add_custom("tr", "bad", "x.gguf")            # Repository shape
    assert "error" in modelhub.add_custom("asr", "a/b", "model.bin")        # gguf only
    assert "error" in modelhub.add_custom("zz", "a/b", "m.gguf")            # Kind

    monkeypatch.setattr(modelhub, "_hf_size", lambda repo, file, headers: 42)
    payload = b"G" * 42
    monkeypatch.setattr(modelhub, "_open", lambda u, h=None, timeout=60.0:
                        FakeResponse(payload, 200, {"Content-Length": "42"}))
    got = modelhub.add_custom("asr", "someone/whisper-small-gguf", "whisper-small-Q8_0.gguf",
                              label="Whisper small", device="cpu")
    assert got["added"] == "custom:hf-whisper-small-q8-0" and got["engine"] == "hf-whisper-small-q8-0"
    assert _wait(lambda: modelhub.status(modelhub.find(got["added"]))["state"] == "ready")
    # Once downloaded it goes into the engine config -- the file name alone, on the device that was picked.
    spec = config.find("asr", "hf-whisper-small-q8-0")
    assert spec and spec["backend"] == "tcpp" and spec["model"] == "whisper-small-Q8_0.gguf"
    assert spec["device"] == "cpu"
    # The same id cannot be added twice.
    assert "error" in modelhub.add_custom("asr", "someone/whisper-small-gguf",
                                          "whisper-small-Q8_0.gguf", engine_id="hf-whisper-small-q8-0")
    # Deleting takes the file, the listing and the engine config together.
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
    assert got == ["model.bin", "sentencepiece.model"]     # README is not downloaded


def test_delete_file_id_cannot_escape_the_model_dir(model_dir, tmp_path):
    """The bug where `file:..` deleted the model directory's parent -- the whole user area."""
    model_dir.mkdir()
    (tmp_path / "IMPORTANT.txt").write_text("x")
    (model_dir / "SenseVoiceSmall-Q8_0.gguf").write_bytes(b"m")
    for bad in ("..", ".", "", "../IMPORTANT.txt", "a/b", "a\\b", ".hidden", "C:x"):
        assert "error" in modelhub.delete("file:" + bad), bad
    assert (tmp_path / "IMPORTANT.txt").exists()
    assert model_dir.exists()
    # A name the catalog manages cannot be deleted through file: -- only through its proper id.
    assert "error" in modelhub.delete("file:SenseVoiceSmall-Q8_0.gguf")
    assert (model_dir / "SenseVoiceSmall-Q8_0.gguf").exists()
    # A symlink must lose the link alone and leave what it points at.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("k")
    (model_dir / "link").symlink_to(outside)
    assert modelhub.delete("file:link")["deleted"]
    assert (outside / "keep").exists() and not (model_dir / "link").exists()


def test_cancel_while_queued_skips_the_download(model_dir, monkeypatch):
    import threading
    gate = threading.Event()
    got = []

    def fake_open(url, headers=None, timeout=60.0):
        gate.wait(5)
        got.append(url)
        return FakeResponse(b"x" * 10, 200, {"Content-Length": "10"})

    monkeypatch.setattr(modelhub, "_open", fake_open)
    modelhub.download(["silero-vad", "campplus"])       # While the first one is held at the gate
    assert modelhub.status(modelhub.find("campplus"))["state"] == "queued"
    assert modelhub.cancel("campplus")["cancelled"] == "campplus"
    gate.set()
    assert _wait(lambda: modelhub.status(modelhub.find("silero-vad"))["state"] == "ready")
    time.sleep(0.1)
    assert modelhub.status(modelhub.find("campplus"))["state"] == "missing"
    assert len(got) == 1                                   # The second one was never downloaded


def test_add_custom_rejects_catalog_names_and_bad_paths(monkeypatch):
    monkeypatch.setattr(modelhub, "_hf_size", lambda repo, file, headers: 1)
    assert "error" in modelhub.add_custom("asr", "a/b", "whisper-large-v3-turbo-Q8_0.gguf")
    assert "error" in modelhub.add_custom("asr", "a/b", "x\\..\\y.gguf")
    assert "error" in modelhub.add_custom("asr", "a/b", "..gguf/z.gguf")
