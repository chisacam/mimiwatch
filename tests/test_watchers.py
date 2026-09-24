"""The addresses the server watches, and the call to start on its own.

The store round-trip and the poller's decisions over a faked probe (no
network, no model loaded: the start the poller makes is faked too, and the
poller only ever reads a session's address and state). The routes are in
`test_server.py`, which owns the module-scoped server.
"""
import json
import subprocess
import time

import live
import store

# ---- store ---------------------------------------------------------------

def test_store_roundtrip(isolated):
    w = store.add_watcher("https://twitch.tv/a", "a")
    assert w["enabled"] and not w["live"]
    # The same address again is an update, not a second line.
    again = store.add_watcher("https://twitch.tv/a", "a2")
    assert again["name"] == "a2"
    store.add_watcher("https://youtube.com/@b")
    assert {w["url"] for w in store.watchers()} == {
        "https://twitch.tv/a", "https://youtube.com/@b"}
    w2 = store.set_watcher("https://twitch.tv/a", enabled=False)
    assert w2 is not None and not w2["enabled"]
    assert store.set_watcher("https://nope", enabled=True) is None
    # The live finding is written; a repeat of the same finding is a no-op
    # (no rewrite of a row the probe keeps confirming).
    store.set_watcher_live("https://youtube.com/@b", True)
    b = [w for w in store.watchers() if w["url"] == "https://youtube.com/@b"][0]
    assert b["live"]
    stamp = b["updated_at"]
    store.set_watcher_live("https://youtube.com/@b", True)
    b = [w for w in store.watchers() if w["url"] == "https://youtube.com/@b"][0]
    assert b["updated_at"] == stamp
    assert store.drop_watcher("https://youtube.com/@b")
    assert not store.drop_watcher("https://youtube.com/@b")
    assert [w["url"] for w in store.watchers()] == ["https://twitch.tv/a"]

# ---- the poller ----------------------------------------------------------

class _Stub:
    """All the poller reads off a session: the address and the state."""
    def __init__(self, url, state):
        self.url, self.state = url, state


def _run_tick(monkeypatch, probes, running=(), live_flags=()):
    """One pass of the poller with the probe and the start faked.

    `probes` maps address to the finding; `running` pairs an address with a
    state already registered; `live_flags` presets the stored finding.
    """
    live._sessions = {"r%d" % i: _Stub(u, st)
                      for i, (u, st) in enumerate(running)}
    started = []

    def fake_start(url, lang, viewer_lang, backend_id, **kw):
        started.append((url, kw.get("title")))
        return {"id": "fake", "source": "hls"}

    monkeypatch.setattr(live, "probe_live", lambda url: probes.get(url))
    monkeypatch.setattr(live, "start", fake_start)
    for url, flag in live_flags:
        store.set_watcher_live(url, flag)
    live._watcher_tick()
    return started


def test_tick_starts_when_live_and_screen_free(isolated, monkeypatch):
    store.add_watcher("u1", "n1")
    started = _run_tick(monkeypatch, {"u1": True})
    assert started == [("u1", "n1")]
    assert [w for w in store.watchers() if w["url"] == "u1"][0]["live"]


def test_tick_waits_while_something_runs(isolated, monkeypatch):
    store.add_watcher("u1")
    started = _run_tick(monkeypatch, {"u1": True}, running=[("other", "running")])
    assert started == []
    # The finding is stored even though nothing was started, so the next
    # pass sees the change without a probe.
    assert [w for w in store.watchers() if w["url"] == "u1"][0]["live"]


def test_tick_waits_when_same_address_is_registered(isolated, monkeypatch):
    store.add_watcher("u1")
    started = _run_tick(monkeypatch, {"u1": True}, running=[("u1", "loading")])
    assert started == []


def test_tick_skips_the_unwilling(isolated, monkeypatch):
    store.add_watcher("u1")
    store.set_watcher("u1", enabled=False)
    probed = []
    started = _run_tick(monkeypatch, {"u1": True})
    monkeypatch.setattr(live, "probe_live", lambda url: probed.append(url) or False)
    _run_tick2 = live._watcher_tick
    _run_tick2()
    assert started == [] and probed == []


def test_unknown_probe_keeps_the_finding(isolated, monkeypatch):
    store.add_watcher("u1")
    store.set_watcher_live("u1", True)          # last seen live
    started = _run_tick(monkeypatch, {"u1": None})
    assert started == []                        # the screen may be free; the probe did not say
    assert [w for w in store.watchers() if w["url"] == "u1"][0]["live"]


# ---- the real probe ------------------------------------------------------
#
# Every test above fakes `probe_live` whole, and that is how a probe that
# raised TypeError on every call (the address went to `ytdlp_args`
# positionally, and it takes it by keyword only) shipped in 0.6.0 with the
# suite green. These run the real one with only the child process faked.

def _fake_ytdlp(monkeypatch, payload, returncode=0):
    """`subprocess.run` answering as yt-dlp would, recording the argv."""
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode,
                                           stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(live.subprocess, "run", fake_run)
    return calls


def test_probe_builds_the_ytdlp_line_and_reads_is_live(monkeypatch):
    calls = _fake_ytdlp(monkeypatch, {"is_live": True})
    assert live.probe_live("https://twitch.tv/a") is True
    argv = calls[0]
    # The address goes last, after `--`, so a pasted `--exec` stays an address.
    assert argv[-2:] == ["--", "https://twitch.tv/a"]
    assert "-j" in argv[:-2]

    _fake_ytdlp(monkeypatch, {"is_live": False})
    assert live.probe_live("https://twitch.tv/a") is False
    _fake_ytdlp(monkeypatch, {"title": "no flag"})
    assert live.probe_live("https://twitch.tv/a") is None
    _fake_ytdlp(monkeypatch, {"is_live": True}, returncode=1)
    assert live.probe_live("https://twitch.tv/a") is None


def test_probe_finds_the_live_entry_on_a_channel_page(monkeypatch):
    _fake_ytdlp(monkeypatch, {"_type": "playlist", "entries": [
        None, {"is_live": False}, {"is_live": True}]})
    assert live.probe_live("https://youtube.com/@b") is True
    _fake_ytdlp(monkeypatch, {"_type": "playlist", "entries": [{"is_live": False}]})
    assert live.probe_live("https://youtube.com/@b") is None


def test_one_probe_that_raises_does_not_end_the_pass(isolated, monkeypatch):
    """A bad address is printed and skipped: the next is still probed, it can
    still start, and the pass still counts as finished."""
    monkeypatch.setattr(live, "_watcher_last_pass", None)
    store.add_watcher("bad", "b")
    store.add_watcher("good", "g")
    # The list comes newest first by a stamp two quick inserts can share, so
    # the order is pinned: the bad one has to come first to be tested at all.
    rows = sorted(store.watchers(), key=lambda w: w["url"] != "bad")
    monkeypatch.setattr(live.store, "watchers", lambda: rows)
    probed = []

    def probe(url):
        probed.append(url)
        if url == "bad":
            raise TypeError("the probe fell over")
        return True

    started = []
    monkeypatch.setattr(live, "probe_live", probe)
    monkeypatch.setattr(live, "start", lambda url, *a, **kw:
                        started.append(url) or {"id": "fake"})
    live._watcher_tick()
    assert probed == ["bad", "good"]
    assert started == ["good"]
    assert live.watcher_status()["last_pass"] is not None


# ---- what the poller says about itself -----------------------------------
#
# A pass that finds nothing changed writes no row and publishes nothing, so
# the finish time kept in the module is the only sign the loop is alive.

def test_status_before_any_pass(isolated, monkeypatch):
    """Never polled is a state of its own, not a very old poll."""
    monkeypatch.setattr(live, "_watcher_last_pass", None)
    st = live.watcher_status()
    assert st["last_pass"] is None
    assert st["poll_s"] == live.WATCH_POLL_S
    assert not st["running"]            # no poller thread in this process


def test_pass_records_when_it_finished(isolated, monkeypatch):
    monkeypatch.setattr(live, "_watcher_last_pass", None)
    store.add_watcher("u1", "n1")
    before = time.time()
    _run_tick(monkeypatch, {"u1": True})
    assert live.watcher_status()["last_pass"] >= before


def test_a_pass_that_changes_nothing_still_records(isolated, monkeypatch):
    """The case the stamp exists for: nothing watched, nothing to write, and
    before this the screen had no way to tell that from a dead thread."""
    monkeypatch.setattr(live, "_watcher_last_pass", None)
    _run_tick(monkeypatch, {})
    assert live.watcher_status()["last_pass"] is not None
