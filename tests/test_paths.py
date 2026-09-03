"""저장소에서 돌 때와 묶음(PyInstaller)으로 돌 때의 파일 자리, 그리고 도구 찾기."""
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
    # 모델 자리만 따로 바꿀 수 있습니다.
    monkeypatch.setenv("MIMIWATCH_MODEL_DIR", "/elsewhere")
    assert paths.model_dir() == "/elsewhere"


def test_ffmpeg_cmd_prefers_path_then_tools_dir(monkeypatch, tmp_path):
    stream.reset_tool_cache()
    monkeypatch.setattr(paths, "which", lambda n: None)
    try:
        stream.ffmpeg_cmd()
        raise AssertionError("없는데 예외가 나지 않았습니다")
    except FileNotFoundError as exc:
        assert "Models & Tools" in str(exc)          # 어디서 받으면 되는지 말해 줍니다
    exe = tmp_path / "ffmpeg"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(paths, "which", lambda n: str(exe))
    assert stream.ffmpeg_cmd() == str(exe)
    stream.reset_tool_cache()


def test_ytdlp_cmd_prefers_standalone_then_module(monkeypatch, tmp_path):
    stream.reset_tool_cache()
    monkeypatch.setattr(stream, "deno_path", lambda: None)     # 이 기계의 deno 는 빼고 봅니다
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
    # 묶음 안에서는 자기 자신을 --ytdlp 로 다시 띄웁니다(app.py 가 받습니다).
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
    assert cmd[-2:] == ["--", "--version"]              # 옵션으로 읽히지 않습니다
    assert "--no-playlist" in cmd and "--no-warnings" in cmd
    assert cmd.index("-g") < cmd.index("--")
    stream.reset_tool_cache()


def test_ytdlp_cmd_passes_the_js_runtime_when_deno_is_found(monkeypatch, tmp_path):
    """유튜브 JS 챌린지용 deno 를 찾으면 경로를 직접 넘깁니다 -- yt-dlp 는 PATH 만 보는데
    Finder 에서 띄운 묶음의 PATH 는 짧습니다."""
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
    """자식에게 물려줄 표준 입출력.

    윈도우에서 부모의 표준 핸들이 성치 않으면 `Popen`이 그것을 복제하다
    `OSError: [WinError 6]`으로 넘어졌습니다 -- 0.3.1의 라이브 세션이
    ffmpeg을 세우지 못하고 죽은 자리입니다.
    """
    class Broken:
        def fileno(self):
            raise OSError(9, "bad file descriptor")

    monkeypatch.setattr(sys, "stderr", Broken())
    assert stream.child_io() == {"stdin": subprocess.DEVNULL,
                                 "stderr": subprocess.DEVNULL}
    monkeypatch.setattr(sys, "stderr", None)            # 창 없이 뜬 프로세스
    assert stream.child_io()["stderr"] == subprocess.DEVNULL
    # 성한 것은 그대로 물려줍니다 -- ffmpeg 의 오류 한 줄이 콘솔이나
    # mimiwatch.log 에 남아야 다음 보고가 진단 가능해집니다.
    with open(os.devnull, "w") as f:
        monkeypatch.setattr(sys, "stderr", f)
        assert stream.child_io()["stderr"] == f.fileno()
    # 부르는 쪽이 stderr 를 직접 잡는 자리에서는 두 번 주지 않습니다.
    assert stream.child_io(stderr=False) == {"stdin": subprocess.DEVNULL}
