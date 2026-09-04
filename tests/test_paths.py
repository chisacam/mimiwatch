"""Where files sit when running from the repo and when running as a bundle (PyInstaller), and how tools are found."""
import os
import stat
import subprocess
import sys

import paths
import stream


def test_source_layout_keeps_repo_local_config_and_data(monkeypatch):
    monkeypatch.delenv("MIMIWATCH_CONFIG", raising=False)
    monkeypatch.delenv("MIMIWATCH_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "frozen", lambda: False)
    assert paths.config_path() == os.path.join(paths.BASE, "backends.json")
    assert paths.data_dir() == os.path.join(paths.BASE, "data")


def test_frozen_layout_moves_config_and_data_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMIWATCH_CONFIG", raising=False)
    monkeypatch.delenv("MIMIWATCH_DATA_DIR", raising=False)
    monkeypatch.setenv("MIMIWATCH_HOME", str(tmp_path))
    monkeypatch.setattr(paths, "frozen", lambda: True)
    assert paths.config_path() == str(tmp_path / "backends.json")
    assert paths.data_dir() == str(tmp_path / "data")
    assert paths.model_dir() == str(tmp_path / "models")
    assert paths.tools_dir() == str(tmp_path / "tools")
    # The model location alone can be moved separately.
    monkeypatch.setenv("MIMIWATCH_MODEL_DIR", "/elsewhere")
    assert paths.model_dir() == "/elsewhere"


def test_ffmpeg_cmd_prefers_path_then_tools_dir(monkeypatch, tmp_path):
    stream.reset_tool_cache()
    monkeypatch.setattr(paths, "which", lambda n: None)
    try:
        stream.ffmpeg_cmd()
        raise AssertionError("없는데 예외가 나지 않았습니다")
    except FileNotFoundError as exc:
        assert "Models & Tools" in str(exc)          # It says where to get it
    exe = tmp_path / "ffmpeg"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(paths, "which", lambda n: str(exe))
    assert stream.ffmpeg_cmd() == str(exe)
    stream.reset_tool_cache()


def test_ytdlp_cmd_prefers_standalone_then_module(monkeypatch, tmp_path):
    stream.reset_tool_cache()
    monkeypatch.setattr(stream, "deno_path", lambda: None)     # Leave this machine's deno out of it
    monkeypatch.delenv("MIMIWATCH_YTDLP_COOKIES", raising=False)
    monkeypatch.delenv("MIMIWATCH_YTDLP_COOKIES_BROWSER", raising=False)
    exe = tmp_path / "yt-dlp"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(paths, "tool", lambda n: str(exe) if n == "yt-dlp" else None)
    assert stream.ytdlp_cmd() == [str(exe)]
    stream.reset_tool_cache()
    monkeypatch.setattr(paths, "tool", lambda n: None)
    monkeypatch.setattr(stream.importlib.util, "find_spec", lambda n: object())
    monkeypatch.setattr(paths, "frozen", lambda: False)
    assert stream.ytdlp_cmd() == [sys.executable, "-m", "yt_dlp"]
    stream.reset_tool_cache()
    # Inside a bundle it launches itself again with --ytdlp (app.py catches that).
    monkeypatch.setattr(paths, "frozen", lambda: True)
    assert stream.ytdlp_cmd() == [sys.executable, "--ytdlp"]
    stream.reset_tool_cache()


def test_ytdlp_args_puts_the_url_behind_a_double_dash(monkeypatch):
    stream.reset_tool_cache()
    monkeypatch.setattr(stream, "deno_path", lambda: None)
    monkeypatch.setattr(paths, "tool", lambda n: None)
    monkeypatch.setattr(stream.importlib.util, "find_spec", lambda n: None)
    monkeypatch.delenv("MIMIWATCH_YTDLP_COOKIES", raising=False)
    monkeypatch.delenv("MIMIWATCH_YTDLP_COOKIES_BROWSER", raising=False)
    cmd = stream.ytdlp_args("-f", "234", "-g", url="--version")
    assert cmd[-2:] == ["--", "--version"]              # It is not read as an option
    assert "--no-playlist" in cmd and "--no-warnings" in cmd
    assert cmd.index("-g") < cmd.index("--")
    stream.reset_tool_cache()


def test_ytdlp_cmd_passes_the_js_runtime_when_deno_is_found(monkeypatch, tmp_path):
    """When the deno for YouTube's JS challenge is found, its path is handed over directly -- yt-dlp
    looks at PATH only, and a bundle launched from Finder has a short PATH."""
    stream.reset_tool_cache()
    monkeypatch.setattr(paths, "tool", lambda n: None)
    monkeypatch.setattr(stream.importlib.util, "find_spec", lambda n: None)
    monkeypatch.delenv("MIMIWATCH_YTDLP_COOKIES", raising=False)
    monkeypatch.delenv("MIMIWATCH_YTDLP_COOKIES_BROWSER", raising=False)
    monkeypatch.setattr(stream, "deno_path", lambda: "/x/deno")
    cmd = stream.ytdlp_cmd()
    assert cmd[cmd.index("--js-runtimes") + 1] == "deno:/x/deno"
    assert "ejs:github" in cmd
    stream.reset_tool_cache()


def test_child_io_never_hands_a_child_a_broken_stderr(monkeypatch):
    """The standard I/O handed down to a child.

    On Windows, when the parent's standard handles are unsound, `Popen` fell over
    with `OSError: [WinError 6]` while duplicating them -- the spot where a live
    session on 0.3.1 died without ever getting ffmpeg up.
    """
    class Broken:
        def fileno(self):
            raise OSError(9, "bad file descriptor")

    monkeypatch.setattr(sys, "stderr", Broken())
    assert stream.child_io() == {"stdin": subprocess.DEVNULL,
                                 "stderr": subprocess.DEVNULL}
    monkeypatch.setattr(sys, "stderr", None)            # A process launched with no console
    assert stream.child_io()["stderr"] == subprocess.DEVNULL
    # A sound one is handed down as it is -- ffmpeg's one line of error has to land
    # in the console or in mimiwatch.log for the next report to be diagnosable.
    with open(os.devnull, "w") as f:
        monkeypatch.setattr(sys, "stderr", f)
        assert stream.child_io()["stderr"] == f.fileno()
    # Where the caller takes stderr itself, it is not handed over twice.
    assert stream.child_io(stderr=False) == {"stdin": subprocess.DEVNULL}
