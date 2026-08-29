#Requires -Version 7
<#
.SYNOPSIS
  윈도우용 묶음을 만듭니다. dist\mimiwatch\mimiwatch.exe 와 그 zip.

.DESCRIPTION
  packaging\build.sh 의 윈도우판입니다. 아무것도 빌드하지 않습니다 -- transcribe.cpp 도
  llama.cpp 도 미리 만들어진 win_amd64 휠을 받습니다. llama-cpp-python 은 PyPI 에
  윈도우 휠이 없어 만든 쪽의 인덱스(cpu / vulkan)를 씁니다. install.ps1 과 같습니다.

  Vulkan 판을 넣으면 GPU 가 없는 기계에서도 돕니다 -- llama.cpp 가 장치를 못 찾으면
  CPU 로 내려갑니다. 그래서 배포용은 vulkan 이 기본입니다.

.EXAMPLE
  .\packaging\build.ps1
  .\packaging\build.ps1 -Backend cpu
  $env:MIMIWATCH_VERSION = '0.3.0'; .\packaging\build.ps1
#>
[CmdletBinding()]
param(
  [ValidateSet('vulkan', 'cpu')] [string] $Backend = 'vulkan'
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
  param([string] $File, [string[]] $Args)
  # install.ps1 과 달리 Start-Process 를 쓰지 않습니다. 액션 러너에서 그것이 PATH 의
  # `python` 을 두 번이나 못 찾았습니다("The system cannot find the file specified").
  # 이 스크립트는 pwsh 7 전용이라 5.1 의 stderr-종료 오류 문제가 없으므로 호출
  # 연산자로 그냥 부릅니다. 출력은 그대로 흘러나옵니다.
  & $File @Args
  if ($LASTEXITCODE -ne 0) { throw "$File $($Args -join ' ') -> 종료 코드 $LASTEXITCODE" }
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
$llamaIndex = "https://abetlen.github.io/llama-cpp-python/whl/$Backend"
Run $Py @('-m', 'pip', 'install', '-q', '--extra-index-url', $llamaIndex,
          '-r', (Join-Path $Root 'requirements.txt'),
          '-r', (Join-Path $Here 'requirements-build.txt'))
Run $Py @('-m', 'pip', 'install', '-q', '-U', 'yt-dlp', 'transcribe-cpp')

Say '런타임 확인'
Run $Py @('-c', 'import transcribe_cpp, llama_cpp, sherpa_onnx, ctranslate2, yt_dlp, certifi; print("  transcribe.cpp:", sorted({b.kind for b in transcribe_cpp.backends()}))')

Say 'PyInstaller'
$dist = Join-Path $Root 'dist'
if (Test-Path (Join-Path $dist 'mimiwatch')) { Remove-Item -Recurse -Force (Join-Path $dist 'mimiwatch') }
Run $Py @('-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
          '--distpath', $dist, '--workpath', (Join-Path $Root 'build'),
          (Join-Path $Here 'mimiwatch.spec'))

Say '동작 확인'
Run (Join-Path $dist 'mimiwatch\mimiwatch.exe') @('--ytdlp', '--version')

Say '압축'
$out = Join-Path $dist "mimiwatch-$($env:MIMIWATCH_VERSION)-windows-x64.zip"
if (Test-Path $out) { Remove-Item $out }
Compress-Archive -Path (Join-Path $dist 'mimiwatch') -DestinationPath $out
Get-Item $out | Format-Table Name, Length
Write-Host "`n끝났습니다: $out" -ForegroundColor White
