"""서버를 시험 포트에 띄워 라우팅·출처 검사·정적 파일 경계를 봅니다.

모델은 올리지 않습니다 -- 두드리는 끝점이 전부 설정·목록·거절 경로입니다.
저장소와 설정은 MIMIWATCH_DATA_DIR / MIMIWATCH_CONFIG 로 임시 디렉터리에 둡니다.
"""
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


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("srv")
    port = _free_port()
    env = {**os.environ, "MIMIWATCH_DATA_DIR": str(tmp / "data"),
           "MIMIWATCH_CONFIG": str(tmp / "backends.json")}
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
