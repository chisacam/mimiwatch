"""Run every check that works without a model, in one go.

    .venv/bin/python bench/check_all.py

What it does: compile every Python module -> run the `bench/*_check.py` style
check scripts in order -> and pytest too, if `tests/` exists and pytest is
installed. Any single failure means exit code 1. Anything that loads a model or
goes out to the network (`tab_ingest.py`, `asr_device.py`, `doctor.py`) does not
belong here.
"""
from __future__ import annotations

import os
import py_compile
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only checks that leave the store alone and load no model.
CHECKS = ["edit_check", "export_check", "ext_check", "live_errors"]


def main() -> int:
    bad = []
    print("[compile]")
    for name in sorted(n for n in os.listdir(HERE) if n.endswith(".py")):
        try:
            py_compile.compile(os.path.join(HERE, name), doraise=True)
        except py_compile.PyCompileError as exc:
            print(f"  FAIL  {name}: {exc}")
            bad.append(name)
    if not bad:
        print("  PASS  every module compiles")

    for name in CHECKS:
        print(f"\n[{name}]")
        r = subprocess.run([sys.executable, os.path.join(HERE, "bench", f"{name}.py")],
                           capture_output=True, text=True)
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        print(f"  {'PASS' if r.returncode == 0 else 'FAIL'}  {tail}")
        if r.returncode != 0:
            print(r.stdout[-2000:], r.stderr[-2000:])
            bad.append(name)

    tests = os.path.join(HERE, "tests")
    if os.path.isdir(tests):
        print("\n[pytest]")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", tests],
                           capture_output=True, text=True)
        if r.returncode == 5 or "No module named pytest" in r.stderr:
            print("  ----  no pytest or no tests (skipped)")
        else:
            print("  " + ("PASS" if r.returncode == 0 else "FAIL") + "  "
                  + (r.stdout.strip().splitlines() or [""])[-1])
            if r.returncode != 0:
                print(r.stdout[-4000:], r.stderr[-2000:])
                bad.append("pytest")

    print("\n" + ("all passed" if not bad else f"failed: {', '.join(bad)}"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
