"""Gathers into one place where this program puts files and where it looks for them.

It runs in two shapes.

  - **From the repository** (`./run.sh`): the screen (`web/`) and the example
    config are inside the repository, and the settings (`backends.json`) and the
    store (`data/`) are put inside the repository too. Just as it always was.
  - **As a bundle** (the executable PyInstaller makes): the screen and the
    example config are inside the bundle (`sys._MEIPASS`), and because the
    inside of a bundle is **read-only**, the settings and the store come out
    into the user area -- the same place the models are already in.

Either way the models are put outside the repository
(`~/.local/share/mimiwatch/models`, on Windows
`%LOCALAPPDATA%\\mimiwatch\\models`). Deleting the repository does not download
6GB again, and swapping the bundle for a new version leaves them as they are.

Environment variables move them one at a time.

  MIMIWATCH_HOME        The root of the user area (models, tools, and the
                        settings and store when running as a bundle)
  MIMIWATCH_MODEL_DIR   Only the models somewhere else
  MIMIWATCH_DATA_DIR    Only the store (subtitle DB, downloaded audio) somewhere else
  MIMIWATCH_CONFIG      Only the config file somewhere else

This module imports no other module in the project. That is because anyone has
to be able to import it -- `config`, `store` and `stream` all lean on it.
"""
from __future__ import annotations

import os
import shutil
import sys


def frozen() -> bool:
    """Are we running as a PyInstaller bundle?"""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


# Where the things inside the bundle (the screen, the example config) sit. When
# running from the repository, the repository.
BASE = sys._MEIPASS if frozen() else os.path.dirname(os.path.abspath(__file__))  # type: ignore[attr-defined]


def home() -> str:
    """The root of the user area. The models are always under here, and so are the
    settings and the store when running as a bundle."""
    env = os.environ.get("MIMIWATCH_HOME")
    if env:
        return env
    if sys.platform == "win32":
        # `~/.local/share` does work, but it is not where program data is looked
        # for on Windows. Put several GB there and the user will not find it.
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "mimiwatch")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "mimiwatch")


def model_dir() -> str:
    return os.environ.get("MIMIWATCH_MODEL_DIR") or os.path.join(home(), "models")


def data_dir() -> str:
    """The subtitle DB and the downloaded audio. Running from the repository it is
    `<repository>/data`, as it always was."""
    env = os.environ.get("MIMIWATCH_DATA_DIR")
    if env:
        return env
    return os.path.join(home(), "data") if frozen() else os.path.join(BASE, "data")


def recordings_dir() -> str:
    """Where a session's own audio is written.

    Beside the store, not inside it: the DB holds text that is cheap to make
    again from the audio, while the audio is the one thing that cannot be made
    again at all. Keeping them in sibling directories means a user who wants to
    clear transcripts can do it without touching the recordings.
    """
    return os.path.join(data_dir(), "recordings")


def config_path() -> str:
    env = os.environ.get("MIMIWATCH_CONFIG")
    if env:
        return env
    if frozen():
        return os.path.join(home(), "backends.json")
    return os.path.join(BASE, "backends.json")


def tools_dir() -> str:
    """Downloaded executable tools (ffmpeg, yt-dlp). Put outside the repository by the
    same rule as the models."""
    return os.path.join(home(), "tools")


def tool(name: str) -> str | None:
    """An executable downloaded into the tools directory. None if it is not there."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    path = os.path.join(tools_dir(), exe)
    return path if os.path.isfile(path) and os.access(path, os.X_OK) else None


# A bundle launched from Finder or the Start menu has a short PATH (on macOS
# about /usr/bin:/bin). The Homebrew ffmpeg that was found fine from a terminal
# is not found there, so the common locations are looked at as well.
_EXTRA_PATH = (["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
                os.path.join(os.path.expanduser("~"), ".deno", "bin")]
               if sys.platform != "win32"
               else [os.path.join(os.path.expanduser("~"), ".deno", "bin")])


def which(name: str) -> str | None:
    """The executable on PATH; failing that the common locations above; failing that
    the tools directory."""
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_PATH:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return tool(name)


def cookies_path() -> str:
    """The YouTube login cookies the extension handed over (Netscape format). They are
    the key to an account, so they are kept apart from the settings and the log rather
    than mixed in with them, and the writer makes the file 0600."""
    return os.path.join(home(), "cookies", "youtube.txt")


def log_path() -> str:
    """The file that catches standard output when the bundle runs without a window."""
    return os.path.join(home(), "mimiwatch.log")
