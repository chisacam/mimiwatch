#Requires -Version 7
<#
.SYNOPSIS
  윈도우용 묶음을 만듭니다. dist\mimiwatch\mimiwatch.exe 와 그 zip.

.DESCRIPTION
  packaging\build.sh 의 윈도우판입니다. 아무것도 빌드하지 않습니다 -- transcribe.cpp 도
  llama.cpp 도 미리 만들어진 win_amd64 휠을 받습니다. llama-cpp-python 은 PyPI 에
  윈도우 휠이 없어 만든 쪽의 인덱스(cpu / vulkan)를 씁니다. install.ps1 과 같습니다.

  Vulkan 판을 넣으면 GPU 가 없는 기계에서도 돕니다 -- llama.cpp 가 장치를 못 찾으면
  CPU 로 내려갑니다. 그래서 배포용 기본은 vulkan 입니다.

  -Backend cuda 는 NVIDIA 전용 판입니다. 번역(llama.cpp)이 CUDA 로 돕니다 -- 전사
  (transcribe.cpp)는 CUDA 휠이 아직 없어 그대로 Vulkan 입니다. CUDA 휠에는 런타임
  DLL(cudart64_12, cublas64_12, cublasLt64_12)이 들어 있지 않으므로 nvidia-*-cu12
  패키지에서 꺼내 llama_cpp\lib 에 함께 둡니다. 묶음이 600MB 남짓 커집니다.
  지원 GPU 는 docs/WINDOWS.md 에 적어 두었습니다(GTX 10 ~ RTX 40, 드라이버 551.61+).

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
  # 매개변수 이름을 $Args 로 두면 안 됩니다 -- PowerShell 의 자동 변수 $args 와 겹쳐
  # 빈 배열이 되고, python 이 인자 없이 실행되어 가상환경이 생기지 않았습니다.
  param([string] $File, [string[]] $ArgList)
  # install.ps1 과 달리 Start-Process 를 쓰지 않습니다. 액션 러너에서 그것이 PATH 의
  # `python` 을 두 번이나 못 찾았습니다("The system cannot find the file specified").
  # 이 스크립트는 pwsh 7 전용이라 5.1 의 stderr-종료 오류 문제가 없으므로 호출
  # 연산자로 그냥 부릅니다. 출력은 그대로 흘러나옵니다.
  & $File @ArgList
  if ($LASTEXITCODE -ne 0) { throw "$File $($ArgList -join ' ') -> 종료 코드 $LASTEXITCODE" }
}

Say "빌드 가상환경 ($Venv)"
if (-not (Test-Path $Py)) {
  # setup-python 액션은 pythonLocation 에 자리를 적어 둡니다. 그것이 있으면 이름 해석에
  # 기대지 않고 그 실행 파일을 씁니다.
  $python = if ($env:pythonLocation) { Join-Path $env:pythonLocation 'python.exe' }
            elseif (Get-Command python -EA SilentlyContinue) { 'python' } else { 'py' }
  Run $python @('-m', 'venv', $Venv)
}
Run $Py @('-m', 'pip', 'install', '-q', '--upgrade', 'pip')
# cuda 는 만든 쪽 인덱스 이름이 cu124 입니다(CUDA 12.4 런타임 · 드라이버 551.61 이상).
$llamaFlavor = if ($Backend -eq 'cuda') { 'cu124' } else { $Backend }
$llamaIndex = "https://abetlen.github.io/llama-cpp-python/whl/$llamaFlavor"
Run $Py @('-m', 'pip', 'install', '-q', '--extra-index-url', $llamaIndex,
          '-r', (Join-Path $Root 'requirements.txt'),
          '-r', (Join-Path $Here 'requirements-build.txt'))
Run $Py @('-m', 'pip', 'install', '-q', '-U', 'yt-dlp', 'transcribe-cpp')

if ($Backend -eq 'cuda') {
  Say 'CUDA 런타임 (cudart · cuBLAS)'
  # llama.cpp 의 CUDA 휠은 cudart64_12.dll·cublas64_12.dll 을 부르지만 담고 있지는
  # 않습니다. NVIDIA 가 PyPI 에 올리는 런타임 패키지에서 꺼내 llama_cpp\lib 에 둡니다 --
  # llama_cpp 는 그 디렉터리를 PATH 앞에 붙이고 나서 DLL 을 열므로 거기 있으면 찾습니다.
  # 12.4.* 로 맞춥니다. cu124 휠과 같은 판이어야 합니다.
  Run $Py @('-m', 'pip', 'install', '-q', 'nvidia-cuda-runtime-cu12==12.4.*', 'nvidia-cublas-cu12==12.4.*')
  # llama_cpp 를 import 해서 경로를 묻지 않습니다. CUDA 휠의 llama.dll 은 cudart 가 없으면
  # 열리지 않고, NVIDIA 드라이버(nvcuda.dll)가 없는 러너에서는 넣은 뒤에도 열리지
  # 않습니다 -- 그래서 이 판은 NVIDIA 기계에서만 돕니다. 경로는 site-packages 로 셈합니다.
  $site = (& $Py -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' | Select-Object -Last 1).Trim()
  $libDir = Join-Path $site 'llama_cpp\lib'
  if (-not (Test-Path $libDir)) { throw "llama_cpp\lib 가 없습니다: $libDir" }
  foreach ($pair in @(@('cuda_runtime', 'cudart64_12.dll'), @('cublas', 'cublas64_12.dll'), @('cublas', 'cublasLt64_12.dll'))) {
    $src = Join-Path $site "nvidia\$($pair[0])\bin\$($pair[1])"
    if (-not (Test-Path $src)) { throw "CUDA 런타임 DLL 이 없습니다: $src" }
    Copy-Item -Force $src (Join-Path $libDir $pair[1])
  }
  Write-Host "  llama_cpp\lib 에 넣음: cudart64_12.dll, cublas64_12.dll, cublasLt64_12.dll"
}

Say '런타임 확인'
# CUDA 판은 여기서 llama_cpp 를 열어 보지 않습니다. 위와 같은 이유로 GPU 없는 러너에서는
# 실패하고, 그것은 묶음의 결함이 아닙니다.
$mods = if ($Backend -eq 'cuda') { 'transcribe_cpp, sherpa_onnx, ctranslate2, yt_dlp, certifi' }
        else { 'transcribe_cpp, llama_cpp, sherpa_onnx, ctranslate2, yt_dlp, certifi' }
Run $Py @('-c', "import $mods; print('  transcribe.cpp:', sorted({b.kind for b in transcribe_cpp.backends()}))")

Say 'PyInstaller'
$dist = Join-Path $Root 'dist'
if (Test-Path (Join-Path $dist 'mimiwatch')) { Remove-Item -Recurse -Force (Join-Path $dist 'mimiwatch') }
Run $Py @('-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
          '--distpath', $dist, '--workpath', (Join-Path $Root 'build'),
          (Join-Path $Here 'mimiwatch.spec'))

Say '동작 확인'
Run (Join-Path $dist 'mimiwatch\mimiwatch.exe') @('--ytdlp', '--version')

Say '압축'
$suffix = if ($Backend -eq 'cuda') { '-cuda' } elseif ($Backend -eq 'cpu') { '-cpu' } else { '' }
$out = Join-Path $dist "mimiwatch-$($env:MIMIWATCH_VERSION)-windows-x64$suffix.zip"
if (Test-Path $out) { Remove-Item $out }
Compress-Archive -Path (Join-Path $dist 'mimiwatch') -DestinationPath $out
Get-Item $out | Format-Table Name, Length
Write-Host "`n끝났습니다: $out" -ForegroundColor White
