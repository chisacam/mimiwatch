"""Gathers into one place where this program puts files and where it looks for them.

It runs from the repository (`./run.sh`). The screen (`web/`) and the example
config are inside the repository, and the settings (`backends.json`) and the
store (`data/`) are put inside the repository too. Just as it always was.

The models are put outside the repository (`~/.local/share/mimiwatch/models`,
on Windows `%LOCALAPPDATA%\\mimiwatch\\models`), beside the downloaded tools and
the login cookies. Deleting the repository, or cloning it again, does not
download 6GB again.

Environment variables move them one at a time.

  MIMIWATCH_HOME        The root of the user area (models, tools, cookies)
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


# The repository: where the screen and the example config sit.
BASE = os.path.dirname(os.path.abspath(__file__))


def home() -> str:
    """The root of the user area: the models, the tools and the login cookies."""
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
    return os.path.join(BASE, "data")


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


# Where the usual installers put ffmpeg and deno, looked at after PATH:
# Homebrew (/opt/homebrew/bin, /usr/local/bin), MacPorts (/opt/local/bin) and
# deno's own installer (~/.deno/bin). The server sees only the PATH it was
# started with, and these reach that PATH only through the user's shell setup.
# This list was written for the PyInstaller bundle, which a Finder launch left
# with about /usr/bin:/bin; the bundle is gone. That it still matters for a
# server started by run.sh is unverified -- the cases it would cover are an
# inference, not a measurement: a server started outside an interactive shell
# (an IDE, a launch agent), or from a terminal opened before deno's installer
# edited the shell profile. modelhub's "Models & Tools" reads this list too, to
# show a tool found here as already on the system.
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


def cookies_dir() -> str:
    """Where the login cookies live. They are the key to an account, so they are kept
    apart from the settings and the log rather than mixed in with them, and the writer
    makes every file 0600."""
    return os.path.join(home(), "cookies")


def cookies_path(site: str = "youtube") -> str:
    """One site's login cookies, in Netscape format.

    One file per site, because a file is written whole. While there was a single
    one, handing over a second site's cookies overwrote the first site's, and
    deleting either took both -- which is the wrong answer to "log in to chzzk as
    well". The caller passes a name it chose, never one off a URL.
    """
    return os.path.join(cookies_dir(), f"{site}.txt")


def cookies_merged_path() -> str:
    """The one file yt-dlp is handed. Every line of a Netscape file carries the
    domain it belongs to, so the sites travel together perfectly well; they are
    kept apart on disk only so that a write or a delete touches one of them."""
    return os.path.join(cookies_dir(), "all.txt")
