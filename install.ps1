#Requires -Version 5.1
<#
.SYNOPSIS
  mimiwatch를 윈도우에 설치합니다.

.DESCRIPTION
  install.sh의 윈도우판입니다. 다만 하는 일이 더 적습니다 -- 맥/리눅스에서는
  transcribe.cpp를 직접 빌드해야 하지만, 윈도우에는 미리 만들어진 휠이
  있어서 컴파일러도 CMake도 Vulkan SDK도 필요 없습니다.

    transcribe-cpp        CPU + Vulkan이 든 win_amd64 휠 (전사 런타임)
    llama-cpp-python      CPU 또는 Vulkan 인덱스의 win_amd64 휠 (번역)

  **AMD GPU는 Vulkan으로 돕니다.** 별도 SDK를 깔 필요가 없습니다 --
  Vulkan 런타임(vulkan-1.dll)은 요즘 그래픽 드라이버에 함께 들어옵니다.
  자세한 배경은 docs/WINDOWS.md 를 보십시오.

  이미 끝난 단계는 건너뜁니다. 중간에 끊기면 그냥 다시 실행하십시오.

.PARAMETER Backend
  auto(기본) / vulkan / cpu.
  auto는 GPU를 보고 정합니다. GPU가 있으면 vulkan, 없으면 cpu입니다.

.PARAMETER SkipGemma
  번역용 Gemma(4.9GB)를 건너뜁니다. 가벼운 M2M-100만 쓰게 되는데
  품질 차이가 큽니다(measurements/RESULTS.md 참조).

.PARAMETER ModelDir
  모델을 둘 곳. 기본은 %LOCALAPPDATA%\mimiwatch\models 입니다.
  여기서 바꾸면 run.ps1 을 실행할 때도 같은 값을 주어야 합니다.

.EXAMPLE
  .\install.ps1
  .\install.ps1 -Backend cpu -SkipGemma
#>
[CmdletBinding()]
param(
  [ValidateSet('auto', 'vulkan', 'cpu')] [string] $Backend = 'auto',
  [switch] $SkipGemma,
  [string] $ModelDir
)

$ErrorActionPreference = 'Stop'
# PowerShell 7.3부터는 네이티브 명령이 0이 아닌 코드로 끝나면 스스로
# 예외를 던질 수 있습니다. 이 스크립트는 종료 코드를 직접 보고 사람이 읽을
# 안내를 붙이므로 그 자동 동작을 끕니다. 5.1에는 이 변수가 없으므로 있을
# 때만 건드립니다 -- 5.1에는 대신 stderr가 종료 오류가 되는 다른 문제가
# 있고, 그쪽은 아래 Invoke-Native/Get-Native 가 맡습니다.
if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ModelDir) {
  $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
  $ModelDir = Join-Path $base 'mimiwatch\models'
}
$Py  = Join-Path $Here '.venv\Scripts\python.exe'

function Say  { param($m) Write-Host "`n> $m" -ForegroundColor White }
function Ok   { param($m) Write-Host "  [OK] $m" -ForegroundColor Green }
function Skip { param($m) Write-Host "  [--] $m" -ForegroundColor DarkGray }
function Die  { param($m) Write-Host "`n[X] $m" -ForegroundColor Red; exit 1 }

# ── 네이티브 명령 ───────────────────────────────────────────────────────
#
# Windows PowerShell 5.1에서는 $ErrorActionPreference='Stop'일 때 네이티브
# 명령이 stderr에 **한 줄이라도** 쓰면 그것이 종료 오류가 됩니다
# (NativeCommandError). `2>$null`로는 막히지 않습니다. 실제로 이 자리에서
# 설치가 멈췄습니다 -- 아직 깔지 않은 패키지를 import 해 보는 확인이
# 트레이스백을 내자 스크립트가 통째로 죽었습니다(이슈 #1).
#
# 이 스크립트에는 stderr가 정상인 자리가 여럿입니다: 위의 확인, curl의 진행
# 막대, pip의 알림. 그래서 네이티브 호출은 전부 아래 둘 중 하나를 거칩니다.
# PS 7의 $PSNativeCommandUseErrorActionPreference 와는 다른 이야기라, 위쪽의
# 그 설정만으로는 5.1이 덮이지 않습니다.

# 출력을 그대로 흘려보내며 부릅니다. 진행 막대처럼 살아 움직여야 하는 것에
# 씁니다. Start-Process는 콘솔 핸들을 물려주므로 stderr가 PowerShell의 오류
# 스트림을 거치지 않습니다 -- NativeCommandError 자체가 생기지 않습니다.
function Invoke-Native {
  param([Parameter(Mandatory)][string] $FilePath,
        [string[]] $Arguments = @())
  # 공백이나 한글이 든 경로가 인자로 갈 수 있으므로 감싸 줍니다.
  $quoted = @($Arguments | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
  })
  $p = Start-Process -FilePath $FilePath -ArgumentList $quoted `
                     -NoNewWindow -Wait -PassThru
  return $p.ExitCode
}

# 출력을 붙잡아 돌려줍니다. 짧은 확인에 씁니다. stderr는 문자열로 바꿔
# 담으므로 오류 레코드가 되지 않습니다.
function Get-Native {
  param([Parameter(Mandatory)][string] $FilePath,
        [string[]] $Arguments = @())
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $lines = & $FilePath @Arguments 2>&1 | ForEach-Object { "$_" }
    return [pscustomobject]@{ Code = $LASTEXITCODE; Lines = @($lines) }
  } finally { $ErrorActionPreference = $old }
}

# 여러 줄짜리 파이썬 조각은 임시 파일로 넘깁니다. -c 로 넘기면 따옴표가
# 셸을 거치면서 깨지기 쉽고, Start-Process 에서는 특히 그렇습니다.
function Invoke-PyFile {
  param([Parameter(Mandatory)][string] $Code,
        [string[]] $Arguments = @(),
        [switch] $Stream)
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ("mimiwatch-" + [guid]::NewGuid().ToString('N') + ".py")
  # Set-Content -Encoding UTF8 은 5.1에서 BOM을 붙이고 7에서는 붙이지
  # 않습니다. 조각에 한글이 들어 있으므로 인코딩을 못 박아 둡니다.
  [IO.File]::WriteAllText($tmp, $Code, (New-Object Text.UTF8Encoding $false))
  try {
    if ($Stream) { return Invoke-Native $Py (@($tmp) + $Arguments) }
    return Get-Native $Py (@($tmp) + $Arguments)
  } finally { Remove-Item -LiteralPath $tmp -EA SilentlyContinue }
}

# 모델 한 개를 받습니다. 끊긴 다운로드가 완성본으로 보이지 않도록 .part로
# 받고 다 받은 뒤에 이름을 바꿉니다 -- install.sh와 같은 규칙입니다.
function Get-Model {
  param([string] $Name, [string] $Url, [string] $Desc)
  $dest = Join-Path $ModelDir $Name
  if (Test-Path $dest) { Skip "$Desc 있음"; return }
  Write-Host "  $Desc 내려받는 중..."
  $part = "$dest.part"
  # curl.exe는 윈도우 10 1803부터 기본으로 들어 있습니다. GB 단위 파일에서
  # Invoke-WebRequest보다 훨씬 빠릅니다 -- 그쪽은 응답을 통째로 메모리에
  # 들고 있다가 마지막에 씁니다.
  $code = Invoke-Native 'curl.exe' @('-fL', '--progress-bar', '-o', $part, $Url)
  if ($code -ne 0) { Remove-Item $part -EA SilentlyContinue; Die "$Desc 내려받기 실패 (curl $code)" }
  Move-Item -Force $part $dest
  Ok $Desc
}

# ---- 0. 준비물 -------------------------------------------------------------
Say '준비물 확인'

$PythonExe = $null
$PythonArgs = @()
foreach ($c in 'python', 'python3', 'py -3') {
  $exe, $pre = ($c -split ' ', 2)
  $pre = if ($pre) { @($pre) } else { @() }
  if (-not (Get-Command $exe -EA SilentlyContinue)) { continue }
  # 윈도우 스토어의 자리표시자 python.exe는 실행하면 스토어를 열 뿐입니다.
  # 버전을 물어보고 답하는 것만 진짜로 봅니다.
  #
  # 성패는 종료 코드가 아니라 **출력의 모양**으로 가립니다. 예전에는
  # `... | Select-Object -First 1` 뒤의 $LASTEXITCODE를 봤는데, 파이프라인이
  # 일찍 끊겨 그 값이 갱신되지 않습니다 -- 새 셸에서는 아예 비어 있어서
  # 파이썬이 깔려 있는데도 없다고 말했습니다. (여러 줄이 나올 수 있어 첫
  # 줄만 봅니다.)
  $probe = Get-Native $exe (@($pre) + @('-c', "import sys; print('%d.%d' % sys.version_info[:2])"))
  $v = ($probe.Lines | Select-Object -First 1)
  if ("$v".Trim() -match '^(\d+)\.(\d+)$') {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -eq 3 -and $minor -ge 10) {
      $PythonExe = $exe
      $PythonArgs = $pre
      Ok "python $v"
      break
    }
    Skip "python $v (3.10 미만이라 쓰지 않습니다)"
  }
}
if (-not $PythonExe) {
  Write-Host @'

  Python 3.10 이상이 없습니다.
    winget install --id Python.Python.3.12
  설치한 뒤 터미널을 새로 열고 다시 실행하십시오.
'@
  Die '준비물을 설치한 뒤 다시 실행하십시오.'
}

$missing = @()
# curl.exe는 윈도우 10 1803부터 기본 탑재입니다. 그보다 오래된 환경에서는
# 모델 내려받기가 통째로 실패하므로 여기서 미리 걸러 냅니다.
if (-not (Get-Command curl.exe -EA SilentlyContinue)) {
  Die 'curl.exe가 없습니다. 윈도우 10 1803 이상이 필요합니다.'
}
# yt-dlp는 준비물이 아닙니다. 파이썬 패키지이므로 아래에서 가상환경 안에
# 최신으로 넣습니다 -- 시스템에 깔린 것은 스스로 갱신되지 않아, 몇 달 지나면
# 유튜브에서 포맷을 하나도 받지 못합니다(이슈 #1).
if (Get-Command 'ffmpeg' -EA SilentlyContinue) { Ok 'ffmpeg' } else { $missing += 'ffmpeg' }
if ($missing.Count -gt 0) {
  $ids = @{ 'ffmpeg' = 'Gyan.FFmpeg' }
  Write-Host "`n  없는 것: $($missing -join ', ')"
  foreach ($m in $missing) { Write-Host "    winget install --id $($ids[$m])" }
  Write-Host '  설치한 뒤 터미널을 새로 열고 다시 실행하십시오.'
  Die '준비물을 설치한 뒤 다시 실행하십시오.'
}

# git도 cmake도 확인하지 않습니다. 윈도우에서는 아무것도 빌드하지 않습니다.

# ---- 1. 백엔드 결정 --------------------------------------------------------
Say '백엔드'
if ($Backend -eq 'auto') {
  $gpus = @()
  try {
    $gpus = @(Get-CimInstance Win32_VideoController -EA Stop |
              Where-Object { $_.Name } | ForEach-Object { $_.Name })
  } catch {
    # WMI가 막혀 있는 환경이 있습니다. 설치를 세우지는 않고 CPU로 내려가되,
    # 왜 그랬는지는 남깁니다 -- 조용히 CPU가 되면 GPU가 있는데도 느린
    # 이유를 알 길이 없습니다.
    Skip "GPU 목록을 읽지 못했습니다: $($_.Exception.Message)"
  }
  # Vulkan 런타임 로더는 드라이버가 깔아 둡니다. 그것이 있으면 GPU 경로를
  # 실제로 탈 수 있다는 뜻이므로, 이름 대신 이 파일의 존재로 판단합니다.
  #
  # 이 자리는 "GPU 경로를 탈 수 있는가"를 알아보는 곳이지 설치를 세우는
  # 곳이 아닙니다. 그래서 실패는 전부 '없다'로 읽습니다 -- $env:SystemRoot가
  # 비어 있거나, 가리키는 드라이브가 없으면 Join-Path와 Test-Path가 예외를
  # 던지는데, 그러면 하필 "GPU를 못 알아봤으니 CPU로 가자"는 이 지점에서
  # 설치가 통째로 멈춥니다.
  $sysroot = if ($env:SystemRoot) { $env:SystemRoot } else { 'C:\Windows' }
  $loader = "$sysroot\System32\vulkan-1.dll"
  $hasLoader = Test-Path -LiteralPath $loader -EA SilentlyContinue
  if ($gpus.Count -gt 0 -and $hasLoader) {
    $Backend = 'vulkan'
    Ok "vulkan ($($gpus -join ', '))"
  } else {
    $Backend = 'cpu'
    if ($gpus.Count -eq 0) { Skip 'GPU를 찾지 못했습니다 -> cpu' }
    else { Skip "vulkan-1.dll이 없습니다 (그래픽 드라이버를 갱신하십시오) -> cpu" }
  }
} else {
  Ok "$Backend (직접 지정)"
}

# ---- 2. 가상환경 -----------------------------------------------------------
Say '가상환경'
if (Test-Path $Py) {
  Skip '있음'
} else {
  $code = Invoke-Native $PythonExe (@($PythonArgs) + @('-m', 'venv', (Join-Path $Here '.venv')))
  if ($code -ne 0) { Die '가상환경 생성 실패' }
  Ok '생성'
}
$code = Invoke-Native $Py @('-m', 'pip', 'install', '-q', '--upgrade', 'pip')
if ($code -ne 0) { Die 'pip 갱신 실패' }

Say '의존성'
# llama-cpp-python은 PyPI에 윈도우 휠이 없습니다. 만든 쪽이 따로 두는
# 인덱스에는 있고, 거기에는 Vulkan 판도 있습니다. requirements.txt에
# 적어 두지 않는 이유는 이 인덱스가 윈도우에서만 필요하기 때문입니다.
$llamaIndex = "https://abetlen.github.io/llama-cpp-python/whl/$Backend"
$code = Invoke-Native $Py @('-m', 'pip', 'install', '--extra-index-url', $llamaIndex,
                           '-r', (Join-Path $Here 'requirements.txt'))
if ($code -eq 0) {
  # yt-dlp만 따로 올립니다. requirements에 이미 있지만 -r 은 이미 깔린 것을
  # 그대로 두므로, 다시 실행해도 판올림이 되지 않습니다. 유튜브가 추출
  # 경로를 바꾸면 낡은 판은 포맷을 하나도 받지 못하므로(이슈 #1), 이 한
  # 줄이 "다시 설치하면 고쳐진다"를 성립시킵니다.
  $code = Invoke-Native $Py @('-m', 'pip', 'install', '-q', '-U', 'yt-dlp')
}
if ($code -ne 0) {
  Die @"
의존성 설치에 실패했습니다.
  llama-cpp-python 휠을 못 찾은 것이라면 -Backend cpu 로 다시 해 보십시오.
  인덱스: $llamaIndex
"@
}
$ytv = (Get-Native $Py @('-m', 'yt_dlp', '--version')).Lines | Select-Object -First 1
Ok "설치 (yt-dlp $ytv)"

# ---- 3. 전사 런타임 --------------------------------------------------------
Say '전사 런타임 (transcribe.cpp)'
# 맥/리눅스는 여기서 소스를 받아 CMake로 빌드하지만, 윈도우 휠에는 CPU와
# Vulkan 백엔드가 DLL로 함께 들어 있습니다. 받아서 풀면 끝입니다.
# 아직 안 깔렸으면 여기서 트레이스백이 납니다. 그것이 정상이고, 그래서
# Get-Native 로 붙잡습니다 -- 그냥 부르면 5.1이 이 트레이스백을 종료 오류로
# 바꿔 설치를 통째로 세웁니다(이슈 #1).
if ((Get-Native $Py @('-c', 'import transcribe_cpp')).Code -eq 0) {
  Skip '설치됨'
} else {
  $code = Invoke-Native $Py @('-m', 'pip', 'install', 'transcribe-cpp')
  if ($code -ne 0) { Die 'transcribe-cpp 설치 실패' }
  Ok '설치'
}
$backendProbe = @'
import transcribe_cpp
kinds = [b.kind for b in transcribe_cpp.backends()]
print('  쓸 수 있는 백엔드: ' + ', '.join(sorted(set(kinds))))
if 'vulkan' not in kinds:
    print('  (GPU 경로가 안 잡혔습니다. 전사는 CPU로 돕니다 -- 그래픽')
    print('   드라이버를 갱신하면 잡힐 수 있습니다.)')
'@
# 백엔드를 못 읽어도 설치를 세우지 않습니다. 알려 주는 것이 목적입니다.
$probe = Invoke-PyFile $backendProbe
if ($probe.Code -eq 0) { $probe.Lines | ForEach-Object { Write-Host $_ } }
else { Skip '백엔드 목록을 읽지 못했습니다 (전사는 CPU로도 돕니다)' }

# ---- 4. 모델 ---------------------------------------------------------------
Say '모델'
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null
$HF = 'https://huggingface.co'
$GH = 'https://github.com/k2-fsa/sherpa-onnx/releases/download'

Get-Model 'silero_vad.onnx' `
  "$GH/asr-models/silero_vad.onnx" `
  'Silero VAD (632KB - 발화 구간 분할)'
Get-Model 'whisper-large-v3-turbo-Q8_0.gguf' `
  "$HF/handy-computer/whisper-large-v3-turbo-gguf/resolve/main/whisper-large-v3-turbo-Q8_0.gguf" `
  'Whisper large-v3-turbo Q8_0 (845MB - 전사)'
# 낮은 사양용 대체 전사기. 3.5배 작고 훨씬 빠릅니다. 기본이 버거운
# 기계에서 「전사」 선택기로 고를 수 있게 항상 받아 둡니다.
Get-Model 'SenseVoiceSmall-Q8_0.gguf' `
  "$HF/handy-computer/SenseVoiceSmall-gguf/resolve/main/SenseVoiceSmall-Q8_0.gguf" `
  'SenseVoice Small Q8_0 (241MB - 가벼운 전사)'
if (-not $SkipGemma) {
  Get-Model 'gemma-4-E4B_q4_0-it.gguf' `
    "$HF/google/gemma-4-E4B-it-qat-q4_0-gguf/resolve/main/gemma-4-E4B_q4_0-it.gguf" `
    'Gemma 4 E4B q4_0 (4.9GB - 번역)'
}

# M2M-100은 여러 파일이라 스냅샷으로 받습니다. Gemma를 건너뛴 설치에서는
# 이것이 유일한 번역기이므로 항상 받습니다.
$m2m = Join-Path $ModelDir 'mojicast-m2m100-ct2'
if (Test-Path $m2m) {
  Skip 'M2M-100 있음'
} else {
  Write-Host '  M2M-100 (473MB - 대체 번역기) 내려받는 중...'
  $getM2M = @'
import sys
from huggingface_hub import snapshot_download
snapshot_download('ishiki-emo/mojicast-m2m100-ct2', local_dir=sys.argv[1])
'@
  $code = Invoke-PyFile $getM2M @($m2m) -Stream
  if ($code -ne 0) { Die 'M2M-100 내려받기 실패' }
  Ok 'M2M-100'
}

# 화자 태그는 녹화본 전용이라 없어도 나머지는 돕니다.
Get-Model 'campplus_sv.onnx' `
  "$GH/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx" `
  'CAM++ (27MB - 녹화본 화자 태그)'

# ---- 5. 설정 ---------------------------------------------------------------
Say '설정'
$cfg = Join-Path $Here 'backends.json'
if (Test-Path $cfg) {
  Skip 'backends.json 있음 (덮어쓰지 않습니다)'
} else {
  Copy-Item (Join-Path $Here 'backends.example.json') $cfg
  Ok 'backends.json 생성'
}
New-Item -ItemType Directory -Force -Path (Join-Path $Here 'data') | Out-Null

# ---- 6. 확인 ---------------------------------------------------------------
Say '확인'
$env:MIMIWATCH_MODEL_DIR = $ModelDir
$verify = @'
import os, sys
sys.path.insert(0, sys.argv[1])
import stream, tcpp_asr                                   # noqa: F401
need = {'silero_vad.onnx': '구간 분할',
        'whisper-large-v3-turbo-Q8_0.gguf': '전사'}
missing = [f'{v}: {k}' for k, v in need.items()
           if not os.path.exists(os.path.join(stream.model_dir(), k))]
if missing:
    print('  없음:\n    ' + '\n    '.join(missing))
    raise SystemExit(1)
print('  모듈 적재 OK')
print('  필수 모델 OK')
'@
$check = Invoke-PyFile $verify @($Here)
$check.Lines | ForEach-Object { Write-Host $_ }
if ($check.Code -ne 0) { Die '설치 확인 실패' }
Ok '설치 확인 완료'

Write-Host "`n설치가 끝났습니다.`n" -ForegroundColor White
Write-Host '  실행: .\run.ps1'
Write-Host '  화면: http://localhost:8900'
if ($ModelDir -notmatch [regex]::Escape('mimiwatch\models')) {
  Write-Host "`n  모델을 기본 위치가 아닌 곳에 두었습니다. 실행할 때도 같은 값을 주십시오:"
  Write-Host "    .\run.ps1 -ModelDir '$ModelDir'"
}
Write-Host "`n자세한 사용법은 README.md, 윈도우 관련은 docs/WINDOWS.md 를 보십시오."
