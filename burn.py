"""Burn the subtitles into the video file itself.

The other exports write files down for a tool to take; this one writes the
line back onto the picture. The path is an ffmpeg run:

  1. The rows are written to an SRT in a scratch directory. A VOD cue carries
     its measured range, so the file is the cue as it was measured.
  2. ffmpeg reads the source once, applies the `subtitles` filter (libass)
     and encodes the video to a new file beside it. The audio is copied, not
     re-encoded -- there is nothing to do to it.

The `subtitles` filter only exists in an ffmpeg built with libass. The build
in a plain Homebrew install does not have it, and a burn that lacks the filter
dies with a bare exit code -- `subtitles_ok()` asks first and says so, so the
screen shows what is missing instead of a number.

Only a local file can be burned. A YouTube or Twitch VOD is transcribed from
an audio rendition, and the video itself is never downloaded, so there is no
file here to burn into. The check is in the caller; this module takes a path
that is known to exist.

The run is long (an encode of the whole video, real time at a stretch), so it
reports progress as a fraction of the duration and can be stopped: `run`
takes callbacks and kills the process when `should_stop` turns true.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

import export
import paths
import stream

# What a burned-in line costs the source: one new mp4 file the size of an
# H.264 encoding. The crf is a quality/size knob; 18 is visually lossless on
# already-compressed content and keeps the file near the source's size.
CRF = 18
PRESET = "medium"

# ffmpeg's progress line, as it prints with -stats: `time=00:01:23.45 ...`.
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


class BurnError(RuntimeError):
    """The burn could not be made. The message is what the screen shows."""


def subtitles_ok() -> bool:
    """Whether this ffmpeg can burn: the `subtitles` filter needs libass.

    An ffmpeg without it is the common case (a stock Homebrew build), and it
    is what turns the run into a bare exit code. Asking the filter list
    settles it before a long encode starts.
    """
    try:
        out = subprocess.run(
            [stream.ffmpeg_cmd(), "-hide_banner", "-filters"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except Exception:
        return False
    for line in (out.stdout or "").splitlines():
        f = line.split()
        # A filter line is `FLAGS  name  in->out  description ...`.
        if len(f) >= 2 and f[1] == "subtitles":
            return True
    return False


def out_path_for(media_path: str) -> str:
    """The burned-in file goes beside its source, named after it.

    The source itself is never touched. A name that is already taken is
    numbered: the first re-burn is `x - mimiwatch (2).mp4`.
    """
    d, base = os.path.split(media_path)
    stem = os.path.splitext(base)[0]
    stem = "".join(c for c in stem if c not in '\\/:*?"<>|').strip() or "video"
    name = f"{stem} - mimiwatch.mp4"
    p = os.path.join(d, name)
    n = 2
    while os.path.exists(p):
        p = os.path.join(d, f"{stem} - mimiwatch ({n}).mp4")
        n += 1
    return p


def filter_path(p: str) -> str:
    """A path as the `subtitles` filter takes it.

    The filter graph is parsed before the shell is, and a value that is not
    quoted has to stand back from the characters the graph uses for structure
    (`:` separates options, `,` and `[]` separate filters). Windows paths turn
    to forward slashes, which ffmpeg takes on Windows as well.
    """
    p = p.replace("\\", "/")
    for c in (":", ",", "[", "]", "'"):
        p = p.replace(c, "\\" + c)
    return p


def write_srt(rows: list[dict], view: str, tmp_dir: str) -> str:
    """The rows as an SRT file in `tmp_dir`, returned as a path."""
    body, _mime = export.render({"title": "burn"}, rows, "srt", view)
    p = os.path.join(tmp_dir, "subs.srt")
    with open(p, "wb") as f:
        f.write(body)
    return p


def plan(media_path: str, srt_path: str, out_path: str) -> list[str]:
    """The ffmpeg command, as a list. A test can read it without running it."""
    return [
        stream.ffmpeg_cmd(),
        "-loglevel", "info", "-stats", "-nostdin", "-y",
        "-i", media_path,
        "-vf", f"subtitles={filter_path(srt_path)}",
        "-c:a", "copy",
        "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET,
        out_path,
    ]


def _time_of(line: str) -> float | None:
    m = _TIME_RE.search(line)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def run(media_path: str, duration: float, rows: list[dict], view: str,
        on_progress=None, should_stop=None) -> dict:
    """Burn the rows into `media_path`. Returns `{"out": path}`.

    `on_progress` takes a fraction 0..1 of the duration; `should_stop` is
    polled between progress lines and kills the run when it turns true. Both
    may be None.

    The scratch SRT is deleted on the way out, and so is a half-written file
    when the run dies: a `x - mimiwatch.mp4` that encodes nothing is a broken
    file with a finished name.
    """
    if not rows:
        raise BurnError("There are no subtitles to burn.")
    if not subtitles_ok():
        raise BurnError(
            "This ffmpeg cannot burn subtitles: it was built without libass. "
            "Use an ffmpeg that has the `subtitles` filter.")
    tmp_dir = tempfile.mkdtemp(prefix="mimiwatch-burn-", dir=paths.data_dir())
    out = out_path_for(media_path)
    partial = out + ".part"
    try:
        srt = write_srt(rows, view, tmp_dir)
        cmd = plan(media_path, srt, partial)
        # It reads stderr itself for progress, so child_io is called with
        # stderr=False -- the handle cannot be given twice.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, **stream.child_io(stderr=False))
        try:
            for line in proc.stderr:
                if should_stop is not None and should_stop():
                    proc.kill()
                    raise BurnError("Stopped.")
                t = _time_of(line or "")
                if t is not None and duration and on_progress is not None:
                    on_progress(min(1.0, t / duration))
            rc = proc.wait()
        except BurnError:
            raise
        except Exception as exc:
            proc.kill()
            raise BurnError(f"ffmpeg died: {exc}") from exc
        if rc != 0:
            raise BurnError(f"ffmpeg exited {rc}.")
        os.replace(partial, out)
        return {"out": out}
    except Exception:
        for p in (partial,):
            if os.path.exists(p):
                os.remove(p)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
