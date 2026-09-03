"""Entry point of the bundle (PyInstaller). Running from the repository uses
`server.py` directly.

One executable wears three faces.

  mimiwatch                  Starts the server and opens the screen in the browser
                             (the double-click case)
  mimiwatch --port 8951      A different port. `--no-browser` does not open the browser
  mimiwatch --ytdlp ...      Acts as yt-dlp -- the bundle has no Python to call
                             `python -m yt_dlp` with, so the server launches
                             itself again like this (`stream.ytdlp_cmd`). It has
                             to be a child process for a time limit to be put on
                             it and for it to be killable.
  mimiwatch --doctor [url]   bench/doctor.py -- prints the prerequisites, the
                             backends and a model load in one go. The bundle has
                             no Python, so that script cannot be run on its own;
                             it is attached here.

When it runs without a window (the macOS .app) standard output goes nowhere.
Then it is turned towards `mimiwatch.log` in the user area -- that is the only
place to see what went wrong.
"""
from __future__ import annotations

import os
import sys


def _ytdlp(argv: list[str]) -> int:
    import yt_dlp
    sys.argv = ["yt-dlp"] + argv
    try:
        yt_dlp.main()
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) or exc.code is None else 1
    return 0


def _trust_store():
    """Tells the Python inside the bundle where the CA list is.

    The OpenSSL that PyInstaller puts in remembers the certificate path of the
    machine it was built on, and on another machine that file is not there. Then
    both Hugging Face and GitHub become CERTIFICATE_VERIFY_FAILED -- the Python
    on macOS does not read the keychain. Pointing at certifi's bundle makes our
    own requests and the yt-dlp that comes up as a child look at it too.
    """
    if not getattr(sys, "frozen", False) or os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass


def _log_to_file():
    """When there is no standard output (a windowless bundle), send it to the log file."""
    import paths
    try:
        if sys.stdout is not None and sys.stdout.isatty():
            return
    except Exception:
        pass
    os.makedirs(paths.home(), exist_ok=True)
    f = open(paths.log_path(), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f


def main() -> int:
    argv = sys.argv[1:]
    _trust_store()
    if argv and argv[0] == "--ytdlp":
        return _ytdlp(argv[1:])
    if argv and argv[0] == "--doctor":
        import runpy
        import paths
        sys.argv = ["doctor"] + argv[1:]
        runpy.run_path(os.path.join(paths.BASE, "bench", "doctor.py"), run_name="__main__")
        return 0
    # The Windows console's default encoding (cp949) mangles Korean titles.
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    _log_to_file()
    want_browser = "--no-browser" not in argv
    argv = [a for a in argv if a != "--no-browser"]
    if want_browser and "--open" not in argv:
        argv.append("--open")
    import server
    # Once the server is done we end without going through finalization -- see
    # server.hard_exit. The other branches are fine with a normal exit: --ytdlp
    # has no model, and --doctor does load a model but lets go of it soon after
    # on the main thread, so no GPU buffer is left when the destructors run.
    server.hard_exit(server.main(argv) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
