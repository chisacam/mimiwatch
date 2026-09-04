"""Export: whose engine's translation goes in, and how the end times are invented."""
import export
import store


def _video():
    store.save_doc("v", {"id": "v", "title": "제목", "source_lang": "ja", "viewer_lang": "ko"})
    store.replace_cues("v", [
        {"start": 0, "end": 1, "text": "a", "translations": {"gemma": "G-a", "local-m2m100": "M-a"}},
        {"start": 2, "end": 3, "text": "b", "translations": {"gemma": "G-b", "local-m2m100": "M-b"}},
    ])


def test_export_uses_requested_backend():
    _video()
    _, rows = export.collect("v", "gemma")
    assert [r["tr"] for r in rows] == ["G-a", "G-b"]
    _, rows = export.collect("v", "local-m2m100")
    assert [r["tr"] for r in rows] == ["M-a", "M-b"]


def test_export_falls_back_when_backend_has_no_translation():
    _video()
    _, rows = export.collect("v", "nope")
    assert rows[0]["tr"] in ("G-a", "M-a")
    _, rows = export.collect("v")
    assert rows[0]["tr"] in ("G-a", "M-a")


def test_live_export_defaults_to_session_backend():
    store.save_session({"id": "s", "state": "stopped", "backend": "gemma", "title": "라이브"})
    store.save_cue("s", {"id": 1, "kind": "final", "t": 1.0, "text": "x", "lang": "ja"})
    store.save_translation("s", 1, "gemma", "G")
    store.save_translation("s", 1, "other", "O")
    meta, rows = export.collect("live:s")
    assert meta["live"] and rows[0]["tr"] == "G"
    _, rows = export.collect("live:s", "other")
    assert rows[0]["tr"] == "O"


def test_live_end_times_are_invented_within_bounds():
    store.save_session({"id": "s", "state": "stopped"})
    for i, t in enumerate([0.0, 0.3, 10.0, 30.0], start=1):
        store.save_cue("s", {"id": i, "kind": "final", "t": t, "text": f"l{i}", "lang": "ja"})
    _, rows = export.collect("live:s")
    ends = [r["end"] - r["start"] for r in rows]
    assert ends[0] == export.END_MIN_S            # A line right on top of the next gets the minimum length
    assert ends[1] == export.END_MAX_S            # 9.7 s until the next line, but the ceiling caps it
    assert ends[3] == export.END_MAX_S            # The last line
    body = export.render({"title": "t"}, rows, "srt", "both")[0].decode()
    assert body.count("-->") == 4
