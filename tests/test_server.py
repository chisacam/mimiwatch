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
SERVER_CONFIG = None
SERVER_HOME = None


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
    # 서버 프로세스의 설정 파일. conftest 가 이 프로세스의 config 를 다른 임시 파일로
    # 돌려 두므로, 서버가 저장한 것을 보려면 그 파일을 직접 읽어야 합니다.
    global SERVER_CONFIG, SERVER_HOME
    SERVER_CONFIG = tmp / "backends.json"
    SERVER_HOME = tmp / "home"
    env = {**os.environ, "MIMIWATCH_DATA_DIR": str(tmp / "data"),
           "MIMIWATCH_CONFIG": str(tmp / "backends.json"),
           "MIMIWATCH_HOME": str(tmp / "home"),
           # SSE 회전을 2초로. 회전 뒤 소켓이 정말 닫히는지 몇 초 안에 봅니다.
           "MIMIWATCH_SSE_ROTATE_S": "2",
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


def test_host_header_must_be_local(server):
    """DNS 리바인딩: 다른 이름으로 들어온 요청은 읽기도 거절합니다."""
    base, port = server
    assert req(base, "/api/videos")[0] == 200                       # 127.0.0.1:port
    assert req(base, "/api/videos", headers={"Host": f"localhost:{port}"})[0] == 200
    assert req(base, "/api/videos", headers={"Host": "evil.example"})[0] == 421
    assert req(base, "/api/videos", headers={"Host": f"evil.example:{port}"})[0] == 421
    assert req(base, "/api/videos", headers={"Host": "127.0.0.1:1"})[0] == 421    # 다른 포트
    s, _ = req(base, "/api/live/stop", body={"id": "x"}, headers={"Host": "evil.example"})
    assert s == 421


def test_api_keys_are_masked_and_kept(server):
    base, _ = server
    entry = {"id": "remote-x", "label": "X", "backend": "openai",
             "base_url": "http://h", "model": "m", "api_key": "sk-secret"}
    s, b = req(base, "/api/backends", body=entry)
    assert s == 200
    got = [x for x in json.loads(b)["backends"] if x["id"] == "remote-x"][0]
    assert got["api_key"] and "sk-secret" not in json.dumps(json.loads(b))    # 가려서 옵니다
    # 가린 값을 그대로 돌려보내면 저장된 키가 남고, 빈 값은 지웁니다.
    req(base, "/api/backends", body={**entry, "label": "X2", "api_key": got["api_key"]})

    def saved():
        cfg = json.loads(SERVER_CONFIG.read_text(encoding="utf-8"))
        return [x for x in cfg["backends"] if x["id"] == "remote-x"][0]

    assert saved()["api_key"] == "sk-secret" and saved()["label"] == "X2"
    req(base, "/api/backends", body={**entry, "api_key": ""})
    assert saved()["api_key"] == ""


def test_active_engine_is_set_on_the_server(server):
    """「관리」 선택기가 서버의 기본 엔진을 바꿉니다 -- 확장이 그 값으로 세션을 시작합니다."""
    base, _ = server
    s, b = req(base, "/api/active", body={"kind": "asr", "id": "tcpp-best"})
    assert s == 200 and json.loads(b)["asr_active"] == "tcpp-best"
    s, b = req(base, "/api/active", body={"kind": "tr", "id": "local-gemma"})
    assert s == 200 and json.loads(b)["active"] == "local-gemma"
    assert req(base, "/api/active", body={"kind": "tr", "id": "nope"})[0] == 400
    assert req(base, "/api/active", body={"kind": "zz", "id": "x"})[0] == 400
    s, b = req(base, "/api/backends")
    assert json.loads(b)["asr_active"] == "tcpp-best"


def test_pushed_cookies_are_stored_privately_and_used(server):
    """확장이 넘긴 쿠키: 0600 으로 저장, 내용은 응답에 없음, yt-dlp 인자에 붙음, 지우면 빠짐."""
    base, _ = server
    s, b = req(base, "/api/cookies")
    assert s == 200 and json.loads(b)["present"] is False
    assert req(base, "/api/cookies/youtube", body={"cookies": "no tabs here"})[0] == 400
    txt = ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tsecret-value\n#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-3PSID\tsecret2\n"
    s, b = req(base, "/api/cookies/youtube", body={"cookies": txt})
    st = json.loads(b)
    assert s == 200 and st["present"] and st["count"] == 2 and "secret" not in b.decode()
    import paths
    path = os.path.join(SERVER_HOME, "cookies", "youtube.txt")
    assert os.path.exists(path)
    if os.name != "nt":
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert "secret-value" in open(path, encoding="utf-8").read()
    # 서버 프로세스의 yt-dlp 인자에 붙는지는 stream 을 같은 HOME 으로 직접 확인합니다.
    import stream
    stream.reset_tool_cache()
    os.environ["MIMIWATCH_HOME"] = str(SERVER_HOME)
    try:
        assert paths.cookies_path() == path
        args = stream._cookie_args()
        assert args[:1] == ["--cookies"] and args[1] == path
    finally:
        del os.environ["MIMIWATCH_HOME"]
        stream.reset_tool_cache()
    s, b = req(base, "/api/cookies/delete", body={})
    assert json.loads(b)["present"] is False and not os.path.exists(path)


def test_static_and_index(server):
    base, _ = server
    assert req(base, "/")[0] == 200
    s, b = req(base, "/static/app/state.js")
    assert s == 200 and b"const state" in b
    s, b = req(base, "/static/vendor/hls.min.js")          # m3u8 재생용 hls.js 가 묶여 있다
    assert s == 200 and len(b) > 100_000 and b"Hls" in b
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


def test_event_bus_rotate_really_closes_the_socket(server):
    """회전(rotate) 뒤 서버가 소켓을 **닫아야** 합니다.

    SSE 응답의 `Connection: keep-alive` 가 close_connection 을 False 로 돌려 놓아,
    핸들러가 그냥 돌아가면 서버는 같은 소켓에서 다음 요청을, 브라우저는 본문이 더
    오길 서로 기다렸습니다. EventSource 는 onerror 없이 조용히 죽어 4.5분마다 자막이
    멎었습니다. 여기서는 회전 프레임 뒤 곧 EOF 가 와야 합니다."""
    _, port = server
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(b"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                  b"Accept: text/event-stream\r\nConnection: keep-alive\r\n\r\n")
        s.settimeout(8)              # 회전 2초 + 여유. 안 닫히면 여기서 timeout 으로 실패
        got, t0 = b"", time.time()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break                # EOF -- 서버가 닫았습니다
            got += chunk
    assert b'data: {"type": "hello"}' in got
    assert b'data: {"type": "rotate"}' in got
    # 회전이 정시에 옵니다(예전에는 15초 keepalive 틱까지 밀렸습니다).
    assert time.time() - t0 < 6


def test_multiview_routes(server):
    base, _ = server
    assert req(base, "/api/multiview", {})[0] == 400
    assert req(base, "/api/multiview", {"sources": "x"})[0] == 400
    assert req(base, "/api/multiview", {"sources": [{"url": ""}]})[0] == 400
    assert req(base, "/api/multiview", {"sources": [{"url": f"https://x/{i}"} for i in range(5)]})[0] == 400
    assert req(base, "/api/multiview/nope")[0] == 404
    assert "error" in json.loads(req(base, "/api/multiview/focus", {"group": "nope", "id": "x"})[1])
    assert "error" in json.loads(req(base, "/api/multiview/add", {"group": "nope", "url": "https://x/a"})[1])
    assert "error" in json.loads(req(base, "/api/multiview/remove", {"group": "nope", "id": "x"})[1])
    assert "error" in json.loads(req(base, "/api/multiview/stop", {"group": "nope"})[1])
    # 탭 소스 둘로 묶음을 만듭니다 -- yt-dlp 도 ffmpeg 도 부르지 않습니다. 시험 서버에는
    # 전사 엔진이 없어 초점을 받은 세션이 곧 오류로 끝나고 초점이 다음으로 넘어가다 묶음이
    # 저절로 없어지므로, 만들어진 모양만 봅니다.
    s, b = req(base, "/api/multiview", {"sources": [{"source": "tab", "title": "a"},
                                                    {"source": "tab", "title": "b"}],
                                        "viewer_lang": "ko", "asr": "no-such-engine"})
    g = json.loads(b)
    assert s == 200 and len(g["members"]) == 2 and g["focus"] == g["members"][0]["id"]
    assert g["members"][0]["focused"] is True and g["members"][1]["focused"] is False
    assert all(m["group"] == g["id"] and m["source"] == "tab" for m in g["members"])
    res = json.loads(req(base, "/api/multiview/stop", {"group": g["id"]})[1])
    assert "stopped" in res or "error" in res


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


def test_upload_and_probe_local(server):
    """로컬 파일: 업로드는 uploads/ 아래에만, probe 는 yt-dlp 없이 바로 답합니다."""
    base, port = server
    import urllib.parse
    q = urllib.parse.quote("나의 클립!.mp3")
    code, body = req(base, f"/api/upload?name={q}", raw=b"abc123")
    assert code == 200
    path = json.loads(body)["path"]
    assert os.path.basename(os.path.dirname(path)) == "uploads"
    with open(path, "rb") as f:
        assert f.read() == b"abc123"
    # 같은 이름을 또 올리면 덮지 않고 딴 이름을 짓습니다.
    code, body2 = req(base, f"/api/upload?name={q}", raw=b"xy")
    assert code == 200 and json.loads(body2)["path"] != path

    code, body = req(base, "/api/probe", body={"url": path})
    d = json.loads(body)
    assert code == 200 and d["id"].startswith("file-") and d["site"] == "file"
    assert d["is_live"] is False and d["media_path"] == path

    # 없는 경로는 무엇이 없는지 바로 말합니다 -- yt-dlp 를 기다리지 않습니다.
    code, body = req(base, "/api/probe", body={"url": "/no/such/파일.mp4"})
    assert code == 400 and "파일이 없습니다" in json.loads(body)["error"]

    # 전사 전에는 내줄 미디어가 없습니다.
    assert req(base, "/api/media/file-doesnotexist")[0] == 404


def test_parse_range():
    """브라우저가 미디어에 보내는 Range 꼴만 받습니다."""
    import server as srv
    assert srv.parse_range(None, 100) == (None, None)
    assert srv.parse_range("bytes=0-", 100) == (0, 99)
    assert srv.parse_range("bytes=10-19", 100) == (10, 19)
    assert srv.parse_range("bytes=90-1000", 100) == (90, 99)   # 끝은 파일 크기로 자릅니다
    assert srv.parse_range("bytes=-10", 100) == (90, 99)       # 마지막 n바이트
    assert srv.parse_range("bytes=100-", 100) == (None, -1)    # 시작이 파일 밖
    assert srv.parse_range("bytes=5-2", 100) == (None, -1)
    assert srv.parse_range("bytes=-0", 100) == (None, -1)
    assert srv.parse_range("units=0-1", 100) == (None, -1)
