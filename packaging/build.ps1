#Requires -Version 5.1
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
  # Start-Process 는 PATH 의 이름을 스스로 풀지 못하는 경우가 있습니다(액션 러너의
  # `python` 이 그랬습니다 -- "The system cannot find the file specified"). 전체
  # 경로로 바꿔 넘깁니다. 이미 경로면 그대로입니다.
  if (-not (Test-Path -LiteralPath $File)) {
    $cmd = Get-Command $File -EA SilentlyContinue
    if ($cmd -and $cmd.Source) { $File = $cmd.Source }
  }
  $p = Start-Process -FilePath $File -ArgumentList $Args -NoNewWindow -Wait -PassThru
  if ($p.ExitCode -ne 0) { throw "$File $($Args -join ' ') -> 종료 코드 $($p.ExitCode)" }
}

Say "빌드 가상환경 ($Venv)"
if (-not (Test-Path $Py)) {
  $python = if (Get-Command python -EA SilentlyContinue) { 'python' } else { 'py' }
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
