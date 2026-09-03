#Requires -Version 7
<#
.SYNOPSIS
  Builds the Windows bundle: dist\mimiwatch\mimiwatch.exe and its zip.

.DESCRIPTION
  The Windows counterpart of packaging\build.sh. It builds nothing -- neither transcribe.cpp
  nor llama.cpp; it fetches prebuilt win_amd64 wheels. llama-cpp-python has no Windows wheel
  on PyPI, so the maintainer's index (cpu / vulkan) is used. Same as install.ps1.

  Putting the Vulkan build in means it also runs on a machine with no GPU -- when llama.cpp
  finds no device it drops to the CPU. That is why the default for release is vulkan.

  -Backend cuda is the NVIDIA-only build. Translation (llama.cpp) runs on CUDA -- transcription
  (transcribe.cpp) has no CUDA wheel yet and stays on Vulkan. The CUDA wheel does not carry the
  runtime DLLs (cudart64_12, cublas64_12, cublasLt64_12), so they are pulled out of the
  nvidia-*-cu12 packages and put in llama_cpp\lib alongside it. The bundle grows by some 600MB.
  The supported GPUs are written down in docs/WINDOWS.md (GTX 10 ~ RTX 40, driver 551.61+).

.EXAMPLE
  .\packaging\build.ps1
  .\packaging\build.ps1 -Backend cuda
  .\packaging\build.ps1 -Backend cpu
  $env:MIMIWATCH_VERSION = '0.3.0'; .\packaging\build.ps1
#>
[CmdletBinding()]
param(
  [ValidateSet('vulkan', 'cuda', 'cpu')] [string] $Backend = 'vulkan'
)
$ErrorActionPreference = 'Stop'
if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
  $PSNativeCommandUseErrorActionPreference = $false
}
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
$Venv = Join-Path $Root '.venv-build'
$Py = Join-Path $Venv 'Scripts\python.exe'
if (-not $env:MIMIWATCH_VERSION) {
  try { $env:MIMIWATCH_VERSION = (git -C $Root describe --tags --always 2>$null) } catch { }
  if (-not $env:MIMIWATCH_VERSION) { $env:MIMIWATCH_VERSION = '0.0.0' }
}

function Say { param($m) Write-Host "`n> $m" -ForegroundColor White }
function Run {
  # The parameter must not be named $Args -- it collides with PowerShell's automatic
  # $args, becomes an empty array, and python then ran with no arguments, so no virtual
  # environment was created.
  param([string] $File, [string[]] $ArgList)
  # Unlike install.ps1, Start-Process is not used. On the Actions runner it twice failed
  # to find the `python` on PATH ("The system cannot find the file specified").
  # This script is pwsh 7 only, so it does not have 5.1's stderr-as-terminating-error
  # problem, and the call operator is enough. The output flows through as-is.
  & $File @ArgList
  if ($LASTEXITCODE -ne 0) { throw "$File $($ArgList -join ' ') -> exit code $LASTEXITCODE" }
}

Say "Build virtual environment ($Venv)"
if (-not (Test-Path $Py)) {
  # The setup-python action writes the place down in pythonLocation. When that is there,
  # use that executable rather than leaning on name resolution.
  $python = if ($env:pythonLocation) { Join-Path $env:pythonLocation 'python.exe' }
            elseif (Get-Command python -EA SilentlyContinue) { 'python' } else { 'py' }
  Run $python @('-m', 'venv', $Venv)
}
Run $Py @('-m', 'pip', 'install', '-q', '--upgrade', 'pip')
# For cuda the maintainer's index is named cu124 (the CUDA 12.4 runtime · driver 551.61 or newer).
$llamaFlavor = if ($Backend -eq 'cuda') { 'cu124' } else { $Backend }
$llamaIndex = "https://abetlen.github.io/llama-cpp-python/whl/$llamaFlavor"
Run $Py @('-m', 'pip', 'install', '-q', '--extra-index-url', $llamaIndex,
          '-r', (Join-Path $Root 'requirements.txt'),
          '-r', (Join-Path $Here 'requirements-build.txt'))
Run $Py @('-m', 'pip', 'install', '-q', '-U', 'yt-dlp[default]', 'transcribe-cpp')

if ($Backend -eq 'cuda') {
  Say 'CUDA runtime (cudart · cuBLAS)'
  # llama.cpp's CUDA wheel calls cudart64_12.dll and cublas64_12.dll but does not carry
  # them. Pull them out of the runtime packages NVIDIA puts on PyPI and put them in
  # llama_cpp\lib -- llama_cpp puts that directory at the front of PATH and then opens the
  # DLLs, so it finds them there. Pinned to 12.4.*, which must match the cu124 wheel.
  Run $Py @('-m', 'pip', 'install', '-q', 'nvidia-cuda-runtime-cu12==12.4.*', 'nvidia-cublas-cu12==12.4.*')
  # It does not ask for the path by importing llama_cpp. The CUDA wheel's llama.dll does not
  # open without cudart, and on a runner with no NVIDIA driver (nvcuda.dll) it does not open
  # even after they are in place -- which is why this build only runs on an NVIDIA machine.
  # The path is worked out from site-packages instead.
  $site = (& $Py -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' | Select-Object -Last 1).Trim()
  $libDir = Join-Path $site 'llama_cpp\lib'
  if (-not (Test-Path $libDir)) { throw "No llama_cpp\lib: $libDir" }
  foreach ($pair in @(@('cuda_runtime', 'cudart64_12.dll'), @('cublas', 'cublas64_12.dll'), @('cublas', 'cublasLt64_12.dll'))) {
    $src = Join-Path $site "nvidia\$($pair[0])\bin\$($pair[1])"
    if (-not (Test-Path $src)) { throw "No CUDA runtime DLL: $src" }
    Copy-Item -Force $src (Join-Path $libDir $pair[1])
  }
  Write-Host "  Put into llama_cpp\lib: cudart64_12.dll, cublas64_12.dll, cublasLt64_12.dll"
}

Say 'Runtime check'
# The CUDA build does not try to open llama_cpp here. For the same reason as above it fails on
# a runner with no GPU, and that is not a defect in the bundle.
$mods = if ($Backend -eq 'cuda') { 'transcribe_cpp, sherpa_onnx, ctranslate2, yt_dlp, certifi' }
        else { 'transcribe_cpp, llama_cpp, sherpa_onnx, ctranslate2, yt_dlp, certifi' }
Run $Py @('-c', "import $mods; print('  transcribe.cpp:', sorted({b.kind for b in transcribe_cpp.backends()}))")

Say 'PyInstaller'
$dist = Join-Path $Root 'dist'
if (Test-Path (Join-Path $dist 'mimiwatch')) { Remove-Item -Recurse -Force (Join-Path $dist 'mimiwatch') }
Run $Py @('-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
          '--distpath', $dist, '--workpath', (Join-Path $Root 'build'),
          (Join-Path $Here 'mimiwatch.spec'))

Say 'Smoke check'
Run (Join-Path $dist 'mimiwatch\mimiwatch.exe') @('--ytdlp', '--version')

Say 'Archiving'
$suffix = if ($Backend -eq 'cuda') { '-cuda' } elseif ($Backend -eq 'cpu') { '-cpu' } else { '' }
$out = Join-Path $dist "mimiwatch-$($env:MIMIWATCH_VERSION)-windows-x64$suffix.zip"
if (Test-Path $out) { Remove-Item $out }
Compress-Archive -Path (Join-Path $dist 'mimiwatch') -DestinationPath $out
Get-Item $out | Format-Table Name, Length
Write-Host "`nDone: $out" -ForegroundColor White
