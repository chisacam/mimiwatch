"""Subtitle burn-in: the ffmpeg plan and the run, without running ffmpeg."""
import os
import subprocess

import burn
import jobs
import store
import stream


def test_plan_shapes_the_command(monkeypatch):
    monkeypatch.setattr(stream, "ffmpeg_cmd", lambda: "/usr/bin/ffmpeg")
    cmd = burn.plan("/movies/a: b.mp4", "/tmp/s'ub.srt", "/movies/out.mp4")
    assert cmd[0].endswith("ffmpeg") or "ffmpeg" in cmd[0]
    assert "-i" in cmd and "/movies/a: b.mp4" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    # The colon and the quote stand back from the filter graph's structure.
    assert vf.startswith("subtitles=/tmp/s") and "\\:" not in vf
    assert "\\'" in vf
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"
    assert cmd[-1] == "/movies/out.mp4"


def test_filter_path_escapes():
    assert burn.filter_path("a/b") == "a/b"
    assert burn.filter_path("a:b,c[d]e'f") == "a\\:b\\,c\\[d\\]e\\'f"
    # The drive's colon is escaped -- it separates options in the filter graph.
    assert burn.filter_path("C:\\x\\y") == "C\\:/x/y"


def test_out_path_never_takes_the_source_and_numbers_collisions():
    d = os.path.dirname(__file__)
    p = os.path.join(d, "movie.mp4")
    out = burn.out_path_for(p)
    assert os.path.split(out)[1] == "movie - mimiwatch.mp4"
    with open(out, "wb"):
        pass
    try:
        out2 = burn.out_path_for(p)
        assert os.path.split(out2)[1] == "movie - mimiwatch (2).mp4"
    finally:
        os.remove(out)


def _fake_popen(monkeypatch, stop_after=None, rc=0):
    """An ffmpeg that writes its progress lines and the output file."""
    monkeypatch.setattr(stream, "ffmpeg_cmd", lambda: "/usr/bin/ffmpeg")
    # The run asks first whether this ffmpeg can burn; let it, so the test is
    # about the encode, not the libass build.
    monkeypatch.setattr(burn, "subtitles_ok", lambda: True)
    seen = {"killed": False}

    class FakeProc:
        def __init__(self, cmd, **kw):
            self.cmd = cmd
            self.rc = rc
            out = cmd[-1]
            with open(out, "wb"):
                pass
            lines = ["frame=  1 fps= 30 time=00:00:10.00",
                     "frame=  2 fps= 29 time=00:01:23.45"]
            if stop_after is not None:
                lines = lines[:stop_after]
            self.stderr = iter(lines)

        def wait(self):
            return self.rc

        def kill(self):
            seen["killed"] = True

    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    return seen


def test_run_returns_the_burned_file(tmp_path, monkeypatch):
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"source")
    rows = [{"start": 0.0, "end": 1.0, "text": "one", "tr": "하나",
             "speaker": "", "kind": "final"}]
    progress = []
    seen = _fake_popen(monkeypatch)
    res = burn.run(str(media), 100.0, rows, "both",
                   on_progress=lambda f: progress.append(f))
    out = tmp_path / "movie - mimiwatch.mp4"
    assert res["out"] == str(out) and out.exists()
    assert not (tmp_path / (out.name + ".part")).exists()
    assert not seen["killed"]
    assert 0 < progress[-1] <= 1.0


def test_run_that_dies_leaves_no_file(tmp_path, monkeypatch):
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"source")
    _fake_popen(monkeypatch, rc=1)
    try:
        burn.run(str(media), 100.0, [{"start": 0, "end": 1, "text": "a",
                                      "tr": "아", "speaker": "", "kind": "final"}],
                 "both")
        assert False, "expected BurnError"
    except burn.BurnError:
        pass
    assert not list(tmp_path.glob("movie - mimiwatch*"))


def test_run_stops_when_asked(tmp_path, monkeypatch):
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"source")
    seen = _fake_popen(monkeypatch)
    try:
        burn.run(str(media), 100.0, [{"start": 0, "end": 1, "text": "a",
                                      "tr": "아", "speaker": "", "kind": "final"}],
                 "both", should_stop=lambda: True)
        assert False, "expected BurnError"
    except burn.BurnError:
        pass
    assert seen["killed"]
    assert not list(tmp_path.glob("movie - mimiwatch*"))


def test_start_burn_runs_to_done(tmp_path, monkeypatch):
    monkeypatch.setattr(stream, "ffmpeg_cmd", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "Popen", _FakeProc)
    monkeypatch.setattr(burn, "subtitles_ok", lambda: True)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"source")
    vid = "burn-vid"
    store.save_doc(vid, {"title": "Movie", "source": "file",
                         "media_path": str(media), "duration": 100.0})
    store.replace_cues(vid, [{"start": 0, "end": 1, "text": "one"}])
    res = jobs.start_burn(vid)
    assert "id" in res
    job_id = res["id"]
    for _ in range(40):
        st = jobs.job_status(job_id)
        if st and st["state"] in ("done", "error"):
            break
        import time
        time.sleep(0.05)
    assert st["state"] == "done", st
    assert os.path.isfile(st["out"])


class _FakeProc:
    """The burn's child: it writes the output file and reports progress."""

    def __init__(self, cmd, **kw):
        self.rc = 0
        with open(cmd[-1], "wb"):
            pass
        self.stderr = iter(["time=00:00:10.00"])

    def wait(self):
        return 0

    def kill(self):
        pass


_FILTERS = ("filters\n" "------\n"
            " TS allpass           A->A       Apply a two-pole all-pass filter.\n"
            " T.S subtitles        V->V       Render subtitle ass/ssa/srt...\n")


def test_subtitles_ok_reads_the_filter_list(monkeypatch):
    class R:
        stdout = _FILTERS

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert burn.subtitles_ok() is True

    class No:
        stdout = _FILTERS.replace(" subtitles ", " drawtext ")

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: No())
    assert burn.subtitles_ok() is False

    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", boom)
    assert burn.subtitles_ok() is False


def test_run_refuses_when_the_build_lacks_libass(tmp_path, monkeypatch):
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"source")
    monkeypatch.setattr(stream, "ffmpeg_cmd", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(burn, "subtitles_ok", lambda: False)
    try:
        burn.run(str(media), 100.0, [{"start": 0, "end": 1, "text": "a",
                                      "tr": "아", "speaker": "", "kind": "final"}],
                 "both")
        assert False, "expected BurnError"
    except burn.BurnError as e:
        assert "libass" in str(e)
    assert not list(tmp_path.glob("movie - mimiwatch*"))


def test_start_burn_refuses_a_video_with_no_file():
    vid = "streamed-vid"
    store.save_doc(vid, {"title": "Streamed"})
    store.replace_cues(vid, [{"start": 0, "end": 1, "text": "one"}])
    res = jobs.start_burn(vid)
    assert "error" in res
