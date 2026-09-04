"""Does the command line come up at all?

Writing "30~50%" into `--no-refine`'s help killed the whole CLI -- argparse reads
the `%` in a help string as a format and raises `badly formed help string`. It is
an error raised while building the parser, so it died right there whatever
arguments were given, and the unit tests never called `main()`, so nobody knew.

Neither a model nor the network is touched -- `--help` builds the parser, prints and exits.
"""
import subprocess
import sys
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# app.py does not use argparse (it reads argv itself). Calling it here would try to
# bring the server up, so it is left out.
@pytest.mark.parametrize("script", ["transcribe_vod.py", "modelhub.py"])
def test_the_command_line_still_builds_its_parser(script, native_stub_path):
    # This is a child process, so it does not inherit the stubs conftest put into
    # sys.modules. On a machine without the runtimes (CI) the stubs written out as files
    # are handed over on PYTHONPATH -- otherwise it dies at `import sherpa_onnx` and this
    # test comes out failing even though the parser is fine.
    env = {**os.environ, "MIMIWATCH_NO_UPDATE_CHECK": "1"}
    if native_stub_path is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(native_stub_path)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    out = subprocess.run([sys.executable, os.path.join(ROOT, script), "--help"],
                         capture_output=True, text=True, timeout=120,
                         cwd=ROOT, env=env)
    assert out.returncode == 0, out.stderr[-800:]
    assert "usage" in out.stdout.lower()
