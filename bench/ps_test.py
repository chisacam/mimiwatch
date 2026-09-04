"""Test install.ps1 on macOS.

It is easy to assume a Windows install script cannot be run without Windows,
but the parts that actually go wrong are mostly platform-independent -- reading
an exit code, passing a here-string as an argument, cleaning up temporary files
on failure, that sort of thing. pwsh runs on macOS too, so those parts are
measured here.

**The script is not copied; line ranges are lifted out of it.** A copy makes
what is tested and what is shipped drift apart, and from then on the test lies.
The anchors are found by the first characters of the code rather than by line
number, so they follow along when the script is edited.

Three things this approach actually caught.

  1. `$LASTEXITCODE` after `& cmd | Select-Object -First 1` is not updated,
     because the pipeline is cut short. In a fresh shell that variable is
     empty, so **it said Python was missing and stopped even though Python was
     installed.**
  2. An empty `$env:SystemRoot` makes `Join-Path` throw. Of all places, the
     install stops wholesale right at "the GPU could not be identified, so go
     with the CPU".
  3. `Test-Path` has the same problem in front of a drive that does not exist.

One thing this harness missed is worth noting too. Windows PowerShell 5.1 turns
a single line of stderr from a native command into a terminating error when
`$ErrorActionPreference='Stop'` (NativeCommandError). pwsh 7 does not behave
that way, so it never surfaced here, and a real Windows user reported it as
issue #1. So [9] below tests it in a form that can be confirmed on pwsh 7 too
-- whether the helper catches stderr and hands back only the exit code.

    pwsh is required:  brew install powershell
    run:               .venv/bin/python bench/ps_test.py
"""
from __future__ import annotations

import os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "install.ps1")


def slice_script():
    """Find, from the code itself, the line ranges of the pieces the test uses."""
    src = open(SCRIPT, encoding="utf-8-sig").read().splitlines()
    find = lambda p: next(i for i, l in enumerate(src, 1) if p(l))
    py_s = find(lambda l: l.startswith("$PythonExe = $null"))
    py_e = find(lambda l: l.startswith("if (-not $PythonExe)")) - 1
    v_s = find(lambda l: l.startswith("$verify = @'"))
    v_e = find(lambda l: l.startswith("$check.Lines"))
    b_s = find(lambda l: l.startswith("Say 'Backend'"))
    b_e = next(i for i, l in enumerate(src, 1) if i > b_s and l == "}")
    fn_s = find(lambda l: l.startswith("function Say"))
    # The helper block runs to the closing brace of Invoke-PyFile. Get-Model used
    # to sit after it, but model downloading moved to modelhub.py (pytest guards it).
    fn_e = next(i for i, l in enumerate(src, 1) if i > fn_s and l == "}"
                and "Remove-Item -LiteralPath $tmp" in src[i - 2])
    # Getting the range wrong runs the model-download stretch as well. That is
    # how 2.2GB got downloaded once.
    assert v_e - v_s < 25, f"the verify block is {v_e - v_s} lines -- the range was taken wrong"
    assert b_e - b_s < 60, f"the backend block is {b_e - b_s} lines -- the range was taken wrong"
    L = lambda a, b: "\n".join(src[a - 1:b])
    return {"fns": L(fn_s, fn_e), "python": L(py_s, py_e),
            "verify": L(v_s, v_e), "backend": L(b_s, b_e),
            "at": f"functions {fn_s}~{fn_e} / python {py_s}~{py_e} / "
                  f"verify {v_s}~{v_e} / backend {b_s}~{b_e}"}


HEAD = """$ErrorActionPreference = 'Stop'
if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
  $PSNativeCommandUseErrorActionPreference = $false
}
"""


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

# [1]~[3] used to test Get-Model (model downloading). Downloading moved to
# modelhub.py, and tests/test_modelhub.py guards the finished file, .part,
# resume and failure.

{p['python']}
Assert ($null -ne $PythonExe) "[4] Python is found even when the previous command exited 1 ($PythonExe)"
$o = & $PythonExe @PythonArgs -c "import sys; print(sys.version_info[0])"
Assert ("$o" -eq '3') '[5] splatting an empty array works'

$Py = Join-Path $env:REPO '.venv/bin/python'
$Here = $env:REPO
$env:MIMIWATCH_MODEL_DIR = $env:REALMODELS
{p['verify']}
Assert ($check.Code -eq 0) '[6] the verify passes with the Python snippet handed over'
$env:MIMIWATCH_MODEL_DIR = $ModelDir
{p['verify']}
Assert ($check.Code -eq 1) '[6b] exit code 1 when the model is missing'

$Backend = 'auto'
{p['backend']}
Assert ($Backend -eq 'cpu') "[7] with no GPU/WMI/drive it does not stop but goes to cpu ($Backend)"

Assert (-not ('{w}' -notmatch [regex]::Escape('{n}'))) '[8] no notice at the default location'
Assert ('D:{chr(92)}models' -notmatch [regex]::Escape('{n}')) '[8] a notice at any other location'

# [9] Regression test for issue #1.
#
# The check that imports a package not installed yet prints a traceback. That
# is normal, and it must not halt the script. On Windows PowerShell 5.1
# $ErrorActionPreference='Stop' turned that stderr into a terminating error and
# killed the whole install. This looks at whether the helper catches stderr and
# hands back only the exit code.
$ErrorActionPreference = 'Stop'
$threw = $false
$res = $null
try {{
  $res = Get-Native $env:SYSPY @('-c', 'import sys; print("boom", file=sys.stderr); sys.exit(3)')
}} catch {{ $threw = $true }}
Assert (-not $threw) '[9] a command writing to stderr does not halt the script'
Assert ($res -and $res.Code -eq 3) "[9] the exit code comes back unchanged ($($res.Code))"
Assert ($res -and ($res.Lines -join ' ') -match 'boom') '[9] the stderr content is caught too'

$threw = $false
try {{
  $code = Invoke-Native $env:SYSPY @('-c', 'import sys; print("boom", file=sys.stderr); sys.exit(4)')
}} catch {{ $threw = $true }}
Assert (-not $threw) '[9] the pass-through path does not halt either'
Assert ($code -eq 4) "[9] the pass-through path returns the exit code too ($code)"

# [10] does a path with a space survive being passed as an argument.
$spaced = Join-Path $ModelDir 'a b'
New-Item -ItemType Directory -Force -Path $spaced | Out-Null
$probe = Get-Native $env:SYSPY @('-c', 'import sys,os; print(os.path.isdir(sys.argv[1]))', $spaced)
Assert (($probe.Lines -join '') -match 'True') '[10] a path with a space is passed through intact'

Write-Host ""
if ($fail) {{ Write-Host "$fail failed" -ForegroundColor Red; exit 1 }}
Write-Host 'all passed' -ForegroundColor Green
"""
    open(f"{tmp}/harness.ps1", "w", encoding="utf-8").write(harness)


def main():
    if not shutil.which("pwsh"):
        sys.exit("pwsh is missing.  brew install powershell")
    p = slice_script()
    print(f"lifted out of install.ps1: {p['at']}")

    tmp = tempfile.mkdtemp(prefix="mimiwatch-pstest-")
    try:
        os.makedirs(f"{tmp}/models")
        # Only the exact name the script calls tests the real call path. macOS
        # has no curl.exe, so a stand-in is put in place.
        os.makedirs(f"{tmp}/shim")
        shim = f"{tmp}/shim/curl.exe"
        open(shim, "w").write('#!/bin/sh\nexec /usr/bin/curl "$@"\n')
        os.chmod(shim, 0o755)
        build(p, tmp)

        # Syntax and static analysis first.
        for f in ("install.ps1", "run.ps1"):
            r = subprocess.run(["pwsh", "-NoProfile", "-Command", f"""
$e=$null; $t=$null
$null=[System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path '{os.path.join(ROOT, f)}'), [ref]$t, [ref]$e)
if ($e.Count) {{ $e | ForEach-Object {{ $_.Message }}; exit 1 }}
'  syntax OK  {f}'"""], capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip())
            if r.returncode:
                return 1

        env = {**os.environ,
               "TESTMODELS": f"{tmp}/models",
               "REPO": ROOT,
               "REALMODELS": os.environ.get(
                   "MIMIWATCH_MODEL_DIR",
                   os.path.expanduser("~/.local/share/mimiwatch/models")),
               # Used in [9]/[10] to emit stderr on purpose. The venv Python is
               # heavy, so the system Python is used.
               "SYSPY": shutil.which("python3") or shutil.which("python") or "python3",
               "PATH": f"{tmp}/shim:{os.environ['PATH']}"}
        r = subprocess.run(["pwsh", "-NoProfile", "-File", f"{tmp}/harness.ps1"],
                           env=env, capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if "PASS" in line or "FAIL" in line or "passed" in line or "failed" in line:
                print(line)
        if r.returncode:
            print(r.stderr[-2000:], file=sys.stderr)
        return r.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
