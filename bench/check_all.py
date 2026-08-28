"""모델 없이 돌 수 있는 검사를 한 번에 돕니다.

    .venv/bin/python bench/check_all.py

하는 일: 모든 파이썬 모듈 컴파일 → `bench/*_check.py` 류의 검사 스크립트를
차례로 실행 → `tests/`가 있고 pytest가 깔려 있으면 그것도. 하나라도 실패하면
종료 코드 1입니다. 모델을 올리거나 네트워크에 나가는 것(`tab_ingest.py`,
`asr_device.py`, `doctor.py`)은 여기 넣지 않습니다.
"""
from __future__ import annotations

import os
import py_compile
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 저장소를 건드리지 않고, 모델도 올리지 않는 검사만.
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
        print("  PASS  모듈 전부 컴파일")

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
            print("  ----  pytest 없음 또는 시험 없음 (건너뜀)")
        else:
            print("  " + ("PASS" if r.returncode == 0 else "FAIL") + "  "
                  + (r.stdout.strip().splitlines() or [""])[-1])
            if r.returncode != 0:
                print(r.stdout[-4000:], r.stderr[-2000:])
                bad.append("pytest")

    print("\n" + ("전부 통과" if not bad else f"실패: {', '.join(bad)}"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
