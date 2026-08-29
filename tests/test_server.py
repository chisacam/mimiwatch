"""서버를 시험 포트에 띄워 라우팅·출처 검사·정적 파일 경계를 봅니다.

모델은 올리지 않습니다 -- 두드리는 끝점이 전부 설정·목록·거절 경로입니다.
저장소와 설정은 MIMIWATCH_DATA_DIR / MIMIWATCH_CONFIG 로 임시 디렉터리에 둡니다.
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _stub_dir(tmp) -> str:
    """전사 런타임이 없는 기계(CI)를 위한 가짜 모듈 디렉터리.

    conftest 의 가짜 모듈은 **이 프로세스**에만 끼워지는데, 서버는 별도 프로세스로
    뜨므로 그쪽에서는 진짜 `import sherpa_onnx` 가 돌아 죽었습니다. 같은 가짜를
    파일로 써서 PYTHONPATH 로 넘깁니다. 진짜가 깔려 있으면 비워 두어 진짜를 씁니다.
    """
    d = tmp / "stubs"
    d.mkdir(exist_ok=True)

    def real(name: str) -> bool:
        # conftest 가 끼운 가짜 모듈에는 __spec__ 이 없어 find_spec 이 ValueError 를
        # 냅니다. 그것도 "진짜가 없다"입니다.
        try:
            return importlib.util.find_spec(name) is not None
        except ValueError:
            return False

    if not real("sherpa_onnx"):
        (d / "sherpa_onnx.py").write_text("# CI용 가짜 모듈. 서버는 import 만 합니다.\n")
    if not real("transcribe_cpp"):
        pkg = d / "transcribe_cpp"
        pkg.mkdir(exist_ok=True)
        (pkg / "__init__.py").write_text(
            "class Model:\n    def __init__(self, *a, **k):\n"
            "        raise RuntimeError('시험은 전사 모델을 올리지 않습니다')\n"
            "def backend_available(name):\n    return False\n"
            "def backends():\n    return []\n")
        (pkg / "errors.py").write_text(
            "class OutputTruncated(Exception):\n    pass\n"
            "class UnsupportedRequest(Exception):\n    pass\n")
    return str(d)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("srv")
    port = _free_port()
    stubs = _stub_dir(tmp)
    env = {**os.environ, "MIMIWATCH_DATA_DIR": str(tmp / "data"),
           "MIMIWATCH_CONFIG": str(tmp / "backends.json"),
           "PYTHONPATH": stubs + (os.pathsep + os.environ["PYTHONPATH"]
                                  if os.environ.get("PYTHONPATH") else "")}
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/api/videos", timeout=1)
            break
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("서버가 죽었습니다:\n" + proc.stdout.read())
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("서버가 뜨지 않았습니다")
    yield base, port
    proc.terminate()
    try:
        proc.communicate(timeout=10)
    except Exception:
        proc.kill()
    # 시험이 진짜 저장소를 건드리지 않았는지.
    assert os.path.exists(tmp / "data" / "mimiwatch.db")


def req(base, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(base + path, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_static_and_index(server):
    base, _ = server
    assert req(base, "/")[0] == 200
    s, b = req(base, "/static/app/state.js")
    assert s == 200 and b"const state" in b
    assert req(base, "/static/../server.py")[0] == 404
    assert req(base, "/static/no-such.js")[0] == 404


def test_json_endpoints(server):
    base, _ = server
    s, b = req(base, "/api/videos")
    assert s == 200 and json.loads(b) == []
    s, b = req(base, "/api/backends")
    cfg = json.loads(b)
    assert cfg["active"] and "genres" in cfg and "live_profiles" in cfg
    assert json.loads(req(base, "/api/live/sessions?limit=5")[1]) == []
    assert req(base, "/api/live/status/nope")[0] == 404
    assert req(base, "/api/job/nope")[0] == 404
    assert req(base, "/api/export?id=nope")[0] == 404
    assert req(base, "/api/nothing")[0] == 404


def test_write_origin_check(server):
    base, port = server
    assert req(base, "/api/live/stop", {"id": "x"}, {"Origin": "https://evil.example"})[0] == 403
    assert req(base, "/api/live/stop", {"id": "x"}, {"Origin": "http://localhost:1234"})[0] == 403
    assert req(base, "/api/live/stop", {"id": "x"}, {"Origin": "chrome-extension://abc"})[0] == 200
    assert req(base, "/api/live/stop", {"id": "x"}, {"Origin": f"http://localhost:{port}"})[0] == 200
    assert req(base, "/api/live/stop", {"id": "x"}, {"Origin": f"http://127.0.0.1:{port}"})[0] == 200
    assert req(base, "/api/live/stop", {"id": "x"})[0] == 200
    assert req(base, "/api/live/stop", {"id": "x"}, {"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_bad_bodies(server):
    base, _ = server
    assert req(base, "/api/live/stop", raw=b"not json")[0] == 400
    assert req(base, "/api/live/stop", raw=b"[1,2]")[0] == 400
    assert req(base, "/api/nothing", {"a": 1})[0] == 404
    s, b = req(base, "/api/ingest/nope", raw=b"\x00" * 200,
               headers={"Content-Type": "application/octet-stream"})
    assert s == 200 and json.loads(b)["error"] == "no such session"


def test_event_bus_stream_says_hello(server):
    base, _ = server
    r = urllib.request.Request(base + "/api/events")
    with urllib.request.urlopen(r, timeout=10) as resp:
        assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
        first = resp.readline()
        assert first.strip() == b'data: {"type": "hello"}'


def test_engine_config_roundtrip(server):
    base, _ = server
    assert "error" in json.loads(req(base, "/api/asr-backends/delete", {"id": "tcpp-best"})[1])
    assert "error" in json.loads(req(base, "/api/backends/delete", {"id": "local-m2m100"})[1])
    s, b = req(base, "/api/backends", {"id": "t-x", "label": "x", "backend": "openai",
                                       "base_url": "http://h", "model": "m"})
    assert any(e["id"] == "t-x" for e in json.loads(b)["backends"])
    s, b = req(base, "/api/backends/delete", {"id": "t-x"})
    assert not any(e["id"] == "t-x" for e in json.loads(b)["backends"])
    assert req(base, "/api/backends", {"label": "no id"})[0] == 400
