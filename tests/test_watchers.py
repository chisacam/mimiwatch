"""The addresses the server watches, and the call to start on its own.

The store round-trip and the poller's decisions over a faked probe (no
network, no model loaded: the start the poller makes is faked too, and the
poller only ever reads a session's address and state). The routes are in
`test_server.py`, which owns the module-scoped server.
"""
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
