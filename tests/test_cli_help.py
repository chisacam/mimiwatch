"""명령줄이 뜨기는 하는가.

`--no-refine` 의 도움말에 「30~50%」를 적었다가 CLI 가 통째로 죽었습니다 --
argparse 는 도움말의 `%` 를 서식으로 읽어 `badly formed help string` 을 냅니다.
파서를 세우다 나는 오류라 어떤 인자를 주든 그 자리에서 죽는데, 단위 시험은
`main()` 을 부르지 않아 아무도 몰랐습니다.

모델도 네트워크도 건드리지 않습니다 -- `--help` 는 파서를 세우고 찍고 나옵니다.
"""
import subprocess
import sys
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# app.py 는 argparse 를 쓰지 않습니다(argv 를 직접 봅니다). 여기서 부르면
# 서버를 띄우려 드는 자리라 넣지 않습니다.
@pytest.mark.parametrize("script", ["transcribe_vod.py", "modelhub.py"])
def test_the_command_line_still_builds_its_parser(script, native_stub_path):
    # 자식 프로세스라 conftest 가 sys.modules 에 끼운 가짜를 물려받지 못합니다. 런타임이
    # 없는 기계(CI)에서는 파일로 적힌 가짜를 PYTHONPATH 로 건네줍니다 -- 그러지 않으면
    # `import sherpa_onnx` 에서 죽고, 파서가 멀쩡한데도 이 시험이 실패로 나옵니다.
    env = {**os.environ, "MIMIWATCH_NO_UPDATE_CHECK": "1"}
    if native_stub_path is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(native_stub_path)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    out = subprocess.run([sys.executable, os.path.join(ROOT, script), "--help"],
                         capture_output=True, text=True, timeout=120,
                         cwd=ROOT, env=env)
    assert out.returncode == 0, out.stderr[-800:]
    assert "usage" in out.stdout.lower()
