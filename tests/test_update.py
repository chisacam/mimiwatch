"""Update: comparing versions, picking the platform's asset, the check cache, and refusing to apply in a repo run.

Nothing goes to the network -- the GitHub query (`_get_json`) is swapped for a fake.
"""
import platform as _platform

import pytest

import update


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    """A fresh state and user area (the updates/ cache) for every test."""
    monkeypatch.setenv("MIMIWATCH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(update, "_state",
                        {"state": "idle", "error": None, "progress": None,
                         "latest": None, "available": False, "checked": 0.0, "file": None})


def test_parse_version():
    assert update.parse_version("v0.3.2") == (0, 3, 2)
    assert update.parse_version("0.10.0") == (0, 10, 0)
    assert update.parse_version("v0.3.2-5-gabc123") == (0, 3, 2)
    assert update.parse_version("gabc123") == ()
    assert update.parse_version("") == ()


def test_is_newer():
    assert update.is_newer("v0.4.0", "v0.3.2")
    assert update.is_newer("v0.10.0", "v0.9.9")          # Compared as numbers, not as strings
    assert not update.is_newer("v0.3.2", "v0.4.0")
    # A working copy ahead of the tag (0.3.2-5-g...) counts as 0.3.2 -- the same tag is not a new version.
    assert not update.is_newer("v0.3.2", "v0.3.2-5-gabc")
    # When the current version cannot be read (a hash with no tag), there is nothing to compare against -- that is not a new version.
    assert not update.is_newer("v0.4.0", "abc123f")


ASSETS = [
    {"name": "mimiwatch-v0.4.0-windows-x64-cuda.zip", "browser_download_url": "u-cuda", "size": 3},
    {"name": "mimiwatch-v0.4.0-macos-arm64.zip", "browser_download_url": "u-mac", "size": 1},
    {"name": "mimiwatch-v0.4.0-windows-x64.zip", "browser_download_url": "u-win", "size": 2},
    {"name": "mimiwatch-ext-v0.4.0.zip", "browser_download_url": "u-ext", "size": 4},
]


def test_pick_asset():
    assert update.pick_asset(ASSETS, "darwin", "arm64")["url"] == "u-mac"
    # The cuda/cpu variants are not automatic update targets. The default (Vulkan) build is picked.
    assert update.pick_asset(ASSETS, "win32", "amd64")["url"] == "u-win"
    assert update.pick_asset(ASSETS, "linux", "x86_64") is None
    assert update.pick_asset([], "darwin", "arm64") is None


def test_check_caches_across_restart(monkeypatch):
    # Picking an asset looks at **this machine's** platform (`pick_asset`'s defaults).
    # What this test wants to see is whether the picked asset comes back unchanged
    # through the one-day cache, so the platform is pinned -- Linux has no matching
    # asset (test_pick_asset, just above), so asset came out None on CI alone and this
    # test was failing for a reason that had nothing to do with the cache.
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(_platform, "machine", lambda: "arm64")

    calls = []

    def fake(url, tok=""):
        calls.append(url)
        return {"tag_name": "v9.9.9", "html_url": "h", "body": "메모",
                "assets": list(ASSETS)}

    monkeypatch.setattr(update, "_get_json", fake)
    monkeypatch.setattr(update, "current_version", lambda: "v0.3.2")
    st = update.check(force=True)
    assert st["available"] is True and st["latest"]["tag"] == "v9.9.9"
    assert st["latest"]["asset"] is not None

    # A re-check within the day never goes to the network.
    update.check()
    assert len(calls) == 1

    # After a restart (in-memory state gone) the cache file still answers.
    update._state.update(checked=0.0, latest=None, available=False)
    st = update.check()
    assert len(calls) == 1 and st["available"] is True

    # force ignores the interval.
    update.check(force=True)
    assert len(calls) == 2


def test_check_failure_is_reported_not_raised(monkeypatch):
    def boom(url, tok=""):
        raise OSError("no network")

    monkeypatch.setattr(update, "_get_json", boom)
    st = update.check(force=True)
    assert st["available"] is False and "check failed" in st["error"]


def test_check_sends_pressed_token(monkeypatch):
    """A token pressed in goes out on the request, and the release's id rides
    on the picked asset so a token can fetch it through the API endpoint."""
    seen = {}

    def fake(url, tok=""):
        seen["tok"] = tok
        return {"tag_name": "v9.9.9", "html_url": "h", "body": "", "id": 7,
                "assets": list(ASSETS)}

    for name in ("MIMIWATCH_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(update, "_get_json", fake)
    st = update.check(force=True, token="sekrit")
    assert seen["tok"] == "sekrit"
    asset = st["latest"]["asset"]
    if asset is not None:
        assert asset["release_id"] == 7


def test_check_falls_back_to_environment_token(monkeypatch):
    seen = {}

    def fake(url, tok=""):
        seen["tok"] = tok
        return {"tag_name": "v9.9.9", "html_url": "h", "body": "", "assets": list(ASSETS)}

    monkeypatch.setenv("MIMIWATCH_GITHUB_TOKEN", "fromenv")
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(update, "_get_json", fake)
    update.check(force=True)
    assert seen["tok"] == "fromenv"
    update._state.update(checked=0.0)
    update.check(force=True, token="pressed")     # The pressed one wins.
    assert seen["tok"] == "pressed"


def test_check_404_reads_as_private_repository(monkeypatch):
    import urllib.error

    def boom(url, tok=""):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(update, "_get_json", boom)
    st = update.check(force=True)
    assert "404" in st["error"] and "token" in st["error"]


def test_get_json_builds_authorization_header(monkeypatch):
    import io

    cap = {}

    def fake_urlopen(req, timeout=None):
        cap["auth"] = req.get_header("Authorization")
        return io.BytesIO(b"{}")

    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen)
    update._get_json("https://api.github.com/x", tok="abc")
    assert cap["auth"] == "Bearer abc"
    update._get_json("https://api.github.com/x")
    assert cap["auth"] is None


def test_download_passes_token_through(monkeypatch):
    got = []

    def fake_download(asset, tok=""):
        got.append(tok)

    monkeypatch.setattr(update, "_download", fake_download)
    update._state.update(available=True, state="idle",
                         latest={"tag": "v0.4.0", "asset": {"name": "a.zip", "url": "u",
                                                           "id": 1, "release_id": 2, "size": 1}})
    update.download(token="tok123")
    update._worker.join(2)
    assert got == ["tok123"]


def test_apply_refuses_repo_run():
    # Running from the repo, there is no bundle to swap in.
    res = update.apply()
    assert "error" in res and "git pull" in res["error"]


def test_download_without_available_refuses():
    assert "error" in update.download()


def test_status_never_touches_network(monkeypatch):
    def boom(url, tok=""):
        raise AssertionError("status() went out to the network")

    monkeypatch.setattr(update, "_get_json", boom)
    st = update.status()
    assert st["current"] and st["frozen"] is False and st["state"] == "idle"
