"""Bring the server up on a test port and watch routing, the origin checks and the static-file boundary.

No model is loaded -- every endpoint knocked on is a config, listing or refusal
path. The store and the config are put in a temporary directory through
MIMIWATCH_DATA_DIR / MIMIWATCH_CONFIG.
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
    """A directory of stub modules for a machine without the transcription runtimes (CI).

    conftest's stubs are installed in **this process** only, and the server comes
    up as a separate process, so over there the real `import sherpa_onnx` ran and
    died. The same stubs are written out as files and handed over on PYTHONPATH.
    When the real ones are installed this is left empty and the real ones are used.
    """
    d = tmp / "stubs"
    d.mkdir(exist_ok=True)

    def real(name: str) -> bool:
        # A stub module installed by conftest has no __spec__, so find_spec raises
        # ValueError. That counts as "the real one is missing" too.
        try:
            return importlib.util.find_spec(name) is not None
        except ValueError:
            return False

    if not real("sherpa_onnx"):
        (d / "sherpa_onnx.py").write_text("# A fake module for CI. The server only imports it.\n")
    if not real("transcribe_cpp"):
        pkg = d / "transcribe_cpp"
        pkg.mkdir(exist_ok=True)
        (pkg / "__init__.py").write_text(
            "class Model:\n    def __init__(self, *a, **k):\n"
            "        raise RuntimeError('tests do not load transcription models')\n"
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
    # The server process's config file. conftest redirects this process's config to a
    # different temporary file, so to see what the server saved that file has to be read
    # directly.
    global SERVER_CONFIG, SERVER_HOME
    SERVER_CONFIG = tmp / "backends.json"
    SERVER_HOME = tmp / "home"
    env = {**os.environ, "MIMIWATCH_DATA_DIR": str(tmp / "data"),
           "MIMIWATCH_CONFIG": str(tmp / "backends.json"),
           "MIMIWATCH_HOME": str(tmp / "home"),
           # A test must never reach GitHub. Turn the automatic update check off.
           "MIMIWATCH_NO_UPDATE_CHECK": "1",
           # SSE rotation down to 2 s, so whether the socket really closes after a rotation shows within seconds.
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
                raise RuntimeError("the server died:\n" + proc.stdout.read())
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("the server never came up")
    yield base, port
    proc.terminate()
    try:
        proc.communicate(timeout=10)
    except Exception:
        proc.kill()
    # Check that the test never touched the real store.
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
    """DNS rebinding: a request that arrives under another name is refused even for reads."""
    base, port = server
    assert req(base, "/api/videos")[0] == 200                       # 127.0.0.1:port
    assert req(base, "/api/videos", headers={"Host": f"localhost:{port}"})[0] == 200
    assert req(base, "/api/videos", headers={"Host": "evil.example"})[0] == 421
    assert req(base, "/api/videos", headers={"Host": f"evil.example:{port}"})[0] == 421
    assert req(base, "/api/videos", headers={"Host": "127.0.0.1:1"})[0] == 421    # A different port
    s, _ = req(base, "/api/live/stop", body={"id": "x"}, headers={"Host": "evil.example"})
    assert s == 421


def test_api_keys_are_masked_and_kept(server):
    base, _ = server
    entry = {"id": "remote-x", "label": "X", "backend": "openai",
             "base_url": "http://h", "model": "m", "api_key": "sk-secret"}
    s, b = req(base, "/api/backends", body=entry)
    assert s == 200
    got = [x for x in json.loads(b)["backends"] if x["id"] == "remote-x"][0]
    assert got["api_key"] and "sk-secret" not in json.dumps(json.loads(b))    # It comes back masked
    # Sending the masked value straight back keeps the stored key; an empty value clears it.
    req(base, "/api/backends", body={**entry, "label": "X2", "api_key": got["api_key"]})

    def saved():
        cfg = json.loads(SERVER_CONFIG.read_text(encoding="utf-8"))
        return [x for x in cfg["backends"] if x["id"] == "remote-x"][0]

    assert saved()["api_key"] == "sk-secret" and saved()["label"] == "X2"
    req(base, "/api/backends", body={**entry, "api_key": ""})
    assert saved()["api_key"] == ""


def test_active_engine_is_set_on_the_server(server):
    """The "Manage" picker changes the server's default engine -- the extension starts a session with that value."""
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
    """Cookies handed over by the extension: stored 0600, never in a response, added to the yt-dlp arguments, gone once deleted."""
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
    # Whether it lands in the server process's yt-dlp arguments is checked directly by pointing stream at the same HOME.
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
    s, b = req(base, "/static/vendor/hls.min.js")          # hls.js for m3u8 playback is bundled
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
    """After a rotate the server **must** close the socket.

    The `Connection: keep-alive` on the SSE response set close_connection back to
    False, so when the handler simply returned the server waited for the next
    request on the same socket while the browser waited for more body -- each on
    the other. EventSource died quietly with no onerror and the subtitles stopped
    every 4.5 minutes. Here EOF has to follow the rotate frame right away."""
    _, port = server
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(b"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                  b"Accept: text/event-stream\r\nConnection: keep-alive\r\n\r\n")
        s.settimeout(8)              # The 2 s rotation plus slack. If it never closes this fails on the timeout
        got, t0 = b"", time.time()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break                # EOF -- the server closed it
            got += chunk
    assert b'data: {"type": "hello"}' in got
    assert b'data: {"type": "rotate"}' in got
    # The rotation arrives on time (it used to be pushed out to the 15 s keepalive tick).
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
    # Build a bundle out of two tab sources -- neither yt-dlp nor ffmpeg is called. The
    # test server has no transcription engine, so the focused session soon ends in an
    # error, the focus moves on and the bundle disappears by itself; only the shape it
    # was created with is examined.
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
    """Local files: an upload lands under uploads/ and nowhere else, and probe answers at once without yt-dlp."""
    base, port = server
    import urllib.parse
    q = urllib.parse.quote("나의 클립!.mp3")
    code, body = req(base, f"/api/upload?name={q}", raw=b"abc123")
    assert code == 200
    path = json.loads(body)["path"]
    assert os.path.basename(os.path.dirname(path)) == "uploads"
    with open(path, "rb") as f:
        assert f.read() == b"abc123"
    # Uploading the same name again does not overwrite; it invents another name.
    code, body2 = req(base, f"/api/upload?name={q}", raw=b"xy")
    assert code == 200 and json.loads(body2)["path"] != path

    code, body = req(base, "/api/probe", body={"url": path})
    d = json.loads(body)
    assert code == 200 and d["id"].startswith("file-") and d["site"] == "file"
    assert d["is_live"] is False and d["media_path"] == path

    # A path that does not exist says so right away -- there is no waiting on yt-dlp.
    code, body = req(base, "/api/probe", body={"url": "/no/such/파일.mp4"})
    assert code == 400 and "No such file" in json.loads(body)["error"]

    # Before transcription there is no media to serve.
    assert req(base, "/api/media/file-doesnotexist")[0] == 404


def test_update_status_endpoint(server):
    """A status query never goes to the network; it answers with the version in hand."""
    base, port = server
    code, body = req(base, "/api/update")
    d = json.loads(body)
    assert code == 200 and d["state"] == "idle" and d["current"]
    assert d["available"] is False


def test_parse_range():
    """Only the Range forms a browser sends for media are accepted."""
    import server as srv
    assert srv.parse_range(None, 100) == (None, None)
    assert srv.parse_range("bytes=0-", 100) == (0, 99)
    assert srv.parse_range("bytes=10-19", 100) == (10, 19)
    assert srv.parse_range("bytes=90-1000", 100) == (90, 99)   # The end is clamped to the file size
    assert srv.parse_range("bytes=-10", 100) == (90, 99)       # The last n bytes
    assert srv.parse_range("bytes=100-", 100) == (None, -1)    # The start is past the end of the file
    assert srv.parse_range("bytes=5-2", 100) == (None, -1)
    assert srv.parse_range("bytes=-0", 100) == (None, -1)
    assert srv.parse_range("units=0-1", 100) == (None, -1)


def test_cue_add_validates(server):
    """Writing a new line: 404 for a video that does not exist, 400 for a request with no timestamp."""
    base, port = server
    code, _ = req(base, "/api/cue/add", body={"id": "nope", "start": 1.0, "text": "x"})
    assert code == 404
    code, _ = req(base, "/api/cue/add", body={"id": "nope", "text": "x"})
    assert code == 400
