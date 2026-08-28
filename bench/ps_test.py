"""install.ps1을 macOS에서 시험합니다.

윈도우 설치 스크립트는 윈도우가 없으면 못 돌린다고 생각하기 쉽지만, 정작
틀리기 쉬운 부분은 대부분 플랫폼과 무관합니다 -- 종료 코드 판정, 여기-문자열
인자 전달, 실패했을 때 임시 파일을 치우는지 같은 것들입니다. pwsh는 macOS
에서도 돌아가므로 그 부분은 여기서 잽니다.

**스크립트를 베껴 쓰지 않고 행 범위로 떼어 옵니다.** 베껴 두면 시험한 것과
배포하는 것이 갈라지고, 그때부터 시험은 거짓말을 합니다. 기준점은 행 번호가
아니라 코드의 첫 글자로 찾으므로 스크립트를 고쳐도 따라옵니다.

이 방식으로 실제로 잡은 것 세 가지입니다.

  1. `& cmd | Select-Object -First 1` 뒤의 `$LASTEXITCODE`는 갱신되지
     않습니다. 파이프라인이 일찍 끊기기 때문입니다. 새 셸에서는 이 변수가
     비어 있어서, **파이썬이 깔려 있어도 없다고 말하고 멈췄습니다.**
  2. `$env:SystemRoot`가 비면 `Join-Path`가 예외를 냅니다. 하필 "GPU를 못
     알아봤으니 CPU로 가자"는 자리에서 설치가 통째로 멈춥니다.
  3. 없는 드라이브 앞에서 `Test-Path`도 같은 문제를 냅니다.

이 하네스가 놓친 것도 하나 적어 둡니다. Windows PowerShell 5.1은
`$ErrorActionPreference='Stop'`일 때 네이티브 명령의 stderr 한 줄을 종료
오류로 바꿉니다(NativeCommandError). pwsh 7에는 그 동작이 없어서 여기서는
드러나지 않았고, 실제 윈도우 사용자가 이슈 #1로 알려 주었습니다. 그래서
아래 [9]는 pwsh 7에서도 확인할 수 있는 형태로 -- 헬퍼가 stderr를 붙잡아
종료 코드만 돌려주는지 -- 시험합니다.

    pwsh 가 필요합니다:  brew install powershell
    실행:                .venv/bin/python bench/ps_test.py
"""
from __future__ import annotations

import os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "install.ps1")


def slice_script():
    """시험에 쓸 조각들의 행 범위를 코드에서 찾아냅니다."""
    src = open(SCRIPT, encoding="utf-8-sig").read().splitlines()
    find = lambda p: next(i for i, l in enumerate(src, 1) if p(l))
    py_s = find(lambda l: l.startswith("$PythonExe = $null"))
    py_e = find(lambda l: l.startswith("if (-not $PythonExe)")) - 1
    v_s = find(lambda l: l.startswith("$verify = @'"))
    v_e = find(lambda l: l.startswith("$check.Lines"))
    b_s = find(lambda l: l.startswith("Say '백엔드'"))
    b_e = next(i for i, l in enumerate(src, 1) if i > b_s and l == "}")
    fn_s = find(lambda l: l.startswith("function Say"))
    fn_e = next(i for i, l in enumerate(src, 1) if i > fn_s and l == "}"
                and src[i - 2].strip().startswith("Ok $Desc"))
    # 범위를 잘못 잡으면 모델 내려받기 구간까지 실행됩니다. 한 번 그렇게
    # 2.2GB를 받았습니다.
    assert v_e - v_s < 25, f"확인 블록이 {v_e - v_s}행 -- 범위를 잘못 잡았습니다"
    assert b_e - b_s < 45, f"백엔드 블록이 {b_e - b_s}행 -- 범위를 잘못 잡았습니다"
    L = lambda a, b: "\n".join(src[a - 1:b])
    return {"fns": L(fn_s, fn_e), "python": L(py_s, py_e),
            "verify": L(v_s, v_e), "backend": L(b_s, b_e),
            "at": f"함수 {fn_s}~{fn_e} / 파이썬 {py_s}~{py_e} / "
                  f"확인 {v_s}~{v_e} / 백엔드 {b_s}~{b_e}"}


HEAD = """$ErrorActionPreference = 'Stop'
if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
  $PSNativeCommandUseErrorActionPreference = $false
}
"""

VAD = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
       "asr-models/silero_vad.onnx")
GONE = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "asr-models/DOES-NOT-EXIST")


def build(p, tmp):
    n = "mimiwatch" + chr(92) + "models"
    w = "C:" + chr(92) + "Users" + chr(92) + "x" + chr(92) + "AppData" + \
        chr(92) + "Local" + chr(92) + n
    harness = f"""{HEAD}$fail = 0
function Assert($c, $w) {{
  if ($c) {{ Write-Host "  PASS  $w" -ForegroundColor Green }}
  else {{ Write-Host "  FAIL  $w" -ForegroundColor Red; $script:fail++ }}
}}
{p['fns']}
$ModelDir = $env:TESTMODELS

Get-Model 'silero_vad.onnx' '{VAD}' 'Silero VAD' | Out-Null
$f = Join-Path $ModelDir 'silero_vad.onnx'
Assert (Test-Path $f) '[1] 내려받아 완성본을 만든다'
Assert (-not (Test-Path "$f.part")) '[1] .part 가 남지 않는다'
$b = (Get-Item $f).LastWriteTime
Get-Model 'silero_vad.onnx' 'https://example.invalid/nope' 'Silero VAD' | Out-Null
Assert ((Get-Item $f).LastWriteTime -eq $b) '[2] 이미 있으면 건드리지 않는다'

$null = & pwsh -NoProfile -File $env:FAILTEST 2>&1
Assert ($LASTEXITCODE -eq 1) '[3] 실패를 exit 1 로 알린다'
Assert (-not (Test-Path (Join-Path $ModelDir 'nope.bin.part'))) '[3] 실패한 .part 를 치운다'

{p['python']}
Assert ($null -ne $PythonExe) "[4] 앞선 명령이 exit 1 이어도 파이썬을 찾는다 ($PythonExe)"
$o = & $PythonExe @PythonArgs -c "import sys; print(sys.version_info[0])"
Assert ("$o" -eq '3') '[5] 빈 배열 스플래팅이 동작한다'

$Py = Join-Path $env:REPO '.venv/bin/python'
$Here = $env:REPO
$env:MIMIWATCH_MODEL_DIR = $env:REALMODELS
{p['verify']}
Assert ($check.Code -eq 0) '[6] 파이썬 조각을 넘겨 확인이 통과한다'
$env:MIMIWATCH_MODEL_DIR = $ModelDir
{p['verify']}
Assert ($check.Code -eq 1) '[6b] 모델이 없으면 종료 코드 1'

$Backend = 'auto'
{p['backend']}
Assert ($Backend -eq 'cpu') "[7] GPU/WMI/드라이브가 없어도 멈추지 않고 cpu 로 간다 ($Backend)"

Assert (-not ('{w}' -notmatch [regex]::Escape('{n}'))) '[8] 기본 위치면 안내하지 않는다'
Assert ('D:{chr(92)}models' -notmatch [regex]::Escape('{n}')) '[8] 다른 위치면 안내한다'

# [9] 이슈 #1의 회귀 시험.
#
# 아직 깔지 않은 패키지를 import 해 보는 확인은 트레이스백을 냅니다. 그것이
# 정상이고, 스크립트를 세워서는 안 됩니다. Windows PowerShell 5.1에서는
# $ErrorActionPreference='Stop'이 그 stderr를 종료 오류로 바꿔 설치를
# 통째로 죽였습니다. 헬퍼가 stderr를 붙잡고 종료 코드만 돌려주는지 봅니다.
$ErrorActionPreference = 'Stop'
$threw = $false
$res = $null
try {{
  $res = Get-Native $env:SYSPY @('-c', 'import sys; print("boom", file=sys.stderr); sys.exit(3)')
}} catch {{ $threw = $true }}
Assert (-not $threw) '[9] stderr를 내는 명령이 스크립트를 세우지 않는다'
Assert ($res -and $res.Code -eq 3) "[9] 종료 코드를 그대로 돌려준다 ($($res.Code))"
Assert ($res -and ($res.Lines -join ' ') -match 'boom') '[9] stderr 내용도 붙잡는다'

$threw = $false
try {{
  $code = Invoke-Native $env:SYSPY @('-c', 'import sys; print("boom", file=sys.stderr); sys.exit(4)')
}} catch {{ $threw = $true }}
Assert (-not $threw) '[9] 흘려보내는 쪽도 세우지 않는다'
Assert ($code -eq 4) "[9] 흘려보내는 쪽도 종료 코드를 돌려준다 ($code)"

# [10] 공백이 든 경로를 인자로 넘겨도 살아남는가.
$spaced = Join-Path $ModelDir 'a b'
New-Item -ItemType Directory -Force -Path $spaced | Out-Null
$probe = Get-Native $env:SYSPY @('-c', 'import sys,os; print(os.path.isdir(sys.argv[1]))', $spaced)
Assert (($probe.Lines -join '') -match 'True') '[10] 공백이 든 경로가 온전히 전달된다'

Write-Host ""
if ($fail) {{ Write-Host "$fail 건 실패" -ForegroundColor Red; exit 1 }}
Write-Host '전부 통과' -ForegroundColor Green
"""
    fail = f"{HEAD}{p['fns']}\n$ModelDir = $env:TESTMODELS\n" \
           f"Get-Model 'nope.bin' '{GONE}' '없는 것'\n"
    open(f"{tmp}/harness.ps1", "w", encoding="utf-8").write(harness)
    open(f"{tmp}/failtest.ps1", "w", encoding="utf-8").write(fail)


def main():
    if not shutil.which("pwsh"):
        sys.exit("pwsh가 없습니다.  brew install powershell")
    p = slice_script()
    print(f"install.ps1에서 떼어 옴: {p['at']}")

    tmp = tempfile.mkdtemp(prefix="mimiwatch-pstest-")
    try:
        os.makedirs(f"{tmp}/models")
        # 스크립트가 부르는 이름 그대로여야 진짜 호출 경로를 시험하는 것이
        # 됩니다. macOS에는 curl.exe가 없으므로 자리를 만들어 줍니다.
        os.makedirs(f"{tmp}/shim")
        shim = f"{tmp}/shim/curl.exe"
        open(shim, "w").write('#!/bin/sh\nexec /usr/bin/curl "$@"\n')
        os.chmod(shim, 0o755)
        build(p, tmp)

        # 구문과 정적 분석부터.
        for f in ("install.ps1", "run.ps1"):
            r = subprocess.run(["pwsh", "-NoProfile", "-Command", f"""
$e=$null; $t=$null
$null=[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path '{os.path.join(ROOT, f)}'), [ref]$t, [ref]$e)
if ($e.Count) {{ $e | ForEach-Object {{ $_.Message }}; exit 1 }}
'  구문 OK  {f}'"""], capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip())
            if r.returncode:
                return 1

        env = {**os.environ,
               "TESTMODELS": f"{tmp}/models",
               "FAILTEST": f"{tmp}/failtest.ps1",
               "REPO": ROOT,
               "REALMODELS": os.environ.get(
                   "MIMIWATCH_MODEL_DIR",
                   os.path.expanduser("~/.local/share/mimiwatch/models")),
               # [9]/[10]에서 stderr를 일부러 내는 데 씁니다. venv 파이썬은
               # 무거우므로 시스템 파이썬을 씁니다.
               "SYSPY": shutil.which("python3") or shutil.which("python") or "python3",
               "PATH": f"{tmp}/shim:{os.environ['PATH']}"}
        r = subprocess.run(["pwsh", "-NoProfile", "-File", f"{tmp}/harness.ps1"],
                           env=env, capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if "PASS" in line or "FAIL" in line or "통과" in line or "실패" in line:
                print(line)
        if r.returncode:
            print(r.stderr[-2000:], file=sys.stderr)
        return r.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
