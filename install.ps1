#Requires -Version 5.1
<#
.SYNOPSIS
  Installs mimiwatch on Windows.

.DESCRIPTION
  The Windows counterpart of install.sh, except that it does less -- on
  macOS/Linux transcribe.cpp used to have to be built, while Windows has
  prebuilt wheels, so neither a compiler nor CMake nor the Vulkan SDK is
  needed.

    transcribe-cpp        the win_amd64 wheel with CPU + Vulkan (transcription runtime)
    llama-cpp-python      the win_amd64 wheel from the CPU or Vulkan index (translation)

  **AMD GPUs run on Vulkan.** There is no separate SDK to install --
  the Vulkan runtime (vulkan-1.dll) comes along with modern graphics drivers.
  For the background see docs/WINDOWS.md.

  Steps that are already done are skipped. If it breaks off midway, just run it again.

.PARAMETER Backend
  auto (the default) / cuda / vulkan / cpu.
  auto looks at the GPU and decides. With a GPU it is vulkan (NVIDIA included), without one cpu.
  cuda is for when you pick it yourself: only translation (llama.cpp) runs on CUDA -- transcription
  has no CUDA wheel yet and stays on Vulkan, and the cu124 wheel carries no RTX 50 (sm_120). It only
  means something on a GTX 10 ~ RTX 40 with driver 551.61 or newer. For the details see docs/WINDOWS.md.

.PARAMETER WithGemma
  Fetch Gemma (4.9GB) for translation as well. The default setup is the light CPU engines
  (SenseVoice Small + M2M-100), so it is not fetched by default. Picking it in
  "First-time setup" on the screen fetches it then.

.PARAMETER ModelDir
  Where to put the models. The default is %LOCALAPPDATA%\mimiwatch\models.
  Change it here and you must give the same value when running run.ps1.

.EXAMPLE
  .\install.ps1
  .\install.ps1 -Backend cpu -WithGemma
#>
[CmdletBinding()]
param(
  [ValidateSet('auto', 'cuda', 'vulkan', 'cpu')] [string] $Backend = 'auto',
  [switch] $WithGemma,
  [string] $ModelDir
)

$ErrorActionPreference = 'Stop'
# From PowerShell 7.3 on, a native command that ends with a non-zero code can
# throw an exception by itself. This script looks at the exit codes itself and
# attaches guidance a person can read, so that automatic behaviour is turned
# off. 5.1 does not have this variable, so it is only touched when it is there
# -- 5.1 has a different problem instead, stderr becoming a terminating error,
# and that one is handled by Invoke-Native/Get-Native below.
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

# ── Native commands ─────────────────────────────────────────────────────
#
# In Windows PowerShell 5.1, when $ErrorActionPreference='Stop', a native
# command writing **even one line** to stderr makes that a terminating error
# (NativeCommandError). `2>$null` does not stop it. The install really did halt
# right here -- a check that tries to import a package which is not installed
# yet printed a traceback, and the whole script died with it (issue #1).
#
# This script has several places where stderr is normal: the check above,
# curl's progress bar, pip's notices. So every native call goes through one of
# the two below. It is a different story from PS 7's
# $PSNativeCommandUseErrorActionPreference, so that setting above does not
# cover 5.1.

# Calls it while letting the output flow through as-is. Used for things that
# have to stay alive, like a progress bar. Start-Process hands over the console
# handle, so stderr never passes through PowerShell's error stream -- a
# NativeCommandError cannot even arise.
function Invoke-Native {
  param([Parameter(Mandatory)][string] $FilePath,
        [string[]] $Arguments = @())
  # A path with spaces or non-ASCII characters can go in as an argument, so wrap it.
  $quoted = @($Arguments | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
  })
  $p = Start-Process -FilePath $FilePath -ArgumentList $quoted `
                     -NoNewWindow -Wait -PassThru
  return $p.ExitCode
}

# Catches the output and returns it. Used for short checks. stderr is turned
# into a string and kept, so it never becomes an error record.
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

# A multi-line piece of Python is handed over as a temporary file. Passing it
# with -c makes the quoting easy to break as it goes through the shell, and
# especially so with Start-Process.
function Invoke-PyFile {
  param([Parameter(Mandatory)][string] $Code,
        [string[]] $Arguments = @(),
        [switch] $Stream)
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ("mimiwatch-" + [guid]::NewGuid().ToString('N') + ".py")
  # Set-Content -Encoding UTF8 adds a BOM on 5.1 and does not on 7. The pieces
  # can hold non-ASCII text, so the encoding is nailed down.
  [IO.File]::WriteAllText($tmp, $Code, (New-Object Text.UTF8Encoding $false))
  try {
    if ($Stream) { return Invoke-Native $Py (@($tmp) + $Arguments) }
    return Get-Native $Py (@($tmp) + $Arguments)
  } finally { Remove-Item -LiteralPath $tmp -EA SilentlyContinue }
}

# ---- 0. Prerequisites ------------------------------------------------------
Say 'Prerequisites'

$PythonExe = $null
$PythonArgs = @()
foreach ($c in 'python', 'python3', 'py -3') {
  $exe, $pre = ($c -split ' ', 2)
  $pre = if ($pre) { @($pre) } else { @() }
  if (-not (Get-Command $exe -EA SilentlyContinue)) { continue }
  # The Windows Store placeholder python.exe only opens the Store when run. Only
  # one that is asked for its version and answers counts as the real thing.
  #
  # Success is told apart by **the shape of the output**, not by the exit code.
  # It used to look at $LASTEXITCODE after `... | Select-Object -First 1`, but
  # the pipeline is cut short early and that value is not updated -- in a fresh
  # shell it is empty altogether, so it said Python was missing when it was
  # installed. (Several lines can come out, so only the first is looked at.)
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
    Skip "python $v (older than 3.10, not used)"
  }
}
if (-not $PythonExe) {
  Write-Host @'

  No Python 3.10 or newer.
    winget install --id Python.Python.3.12
  Install it, then open a new terminal and run this again.
'@
  Die 'Install the prerequisites and run this again.'
}

# yt-dlp is not a prerequisite. It is a Python package, so it goes into the
# virtual environment at its latest below -- a copy installed system-wide does
# not update itself, and a few months on it gets no formats at all from YouTube
# (issue #1).
# ffmpeg is not a prerequisite either but a recommendation. Without it you can
# fetch a static build from "Engine management › Models and tools" on the screen
# -- the last check step says so.
if (Get-Command 'ffmpeg' -EA SilentlyContinue) { Ok 'ffmpeg' }
else { Skip 'no ffmpeg (winget install --id Gyan.FFmpeg, or fetch it from the screen later)' }

# Neither git nor cmake is checked. Nothing is built on Windows.

# ---- 1. Deciding the backend -----------------------------------------------
Say 'Backend'
if ($Backend -eq 'auto') {
  $gpus = @()
  try {
    $gpus = @(Get-CimInstance Win32_VideoController -EA Stop |
              Where-Object { $_.Name } | ForEach-Object { $_.Name })
  } catch {
    # There are environments where WMI is blocked. Do not halt the install --
    # drop to the CPU, but leave behind why it happened. Turning quietly to the
    # CPU leaves no way to tell why it is slow when there is a GPU.
    Skip "Could not read the GPU list: $($_.Exception.Message)"
  }
  # The Vulkan runtime loader is installed by the driver. Its presence means the
  # GPU path can actually be taken, so the decision rests on this file existing
  # rather than on the names.
  #
  # This spot is about finding out "can the GPU path be taken", not about
  # halting the install. So every failure is read as "there is none" -- when
  # $env:SystemRoot is empty, or the drive it points at is missing, Join-Path
  # and Test-Path throw, and then, of all places, the install halts entirely at
  # the "we could not detect a GPU, so let us go CPU" spot.
  $sysroot = if ($env:SystemRoot) { $env:SystemRoot } else { 'C:\Windows' }
  $loader = "$sysroot\System32\vulkan-1.dll"
  $hasLoader = Test-Path -LiteralPath $loader -EA SilentlyContinue
  # NVIDIA is vulkan automatically too. The CUDA build (cu124) carries no RTX 50, so it does
  # not open on the newest cards; someone who knows can pick it with -Backend cuda themselves.
  if ($gpus.Count -gt 0 -and $hasLoader) {
    $Backend = 'vulkan'
    Ok "vulkan ($($gpus -join ', '))"
  } else {
    $Backend = 'cpu'
    if ($gpus.Count -eq 0) { Skip 'No GPU found -> cpu' }
    else { Skip "No vulkan-1.dll (update the graphics driver) -> cpu" }
  }
} else {
  Ok "$Backend (given explicitly)"
}

# ---- 2. Virtual environment ------------------------------------------------
Say 'Virtual environment'
if (Test-Path $Py) {
  Skip 'present'
} else {
  $code = Invoke-Native $PythonExe (@($PythonArgs) + @('-m', 'venv', (Join-Path $Here '.venv')))
  if ($code -ne 0) { Die 'Failed to create the virtual environment' }
  Ok 'created'
}
$code = Invoke-Native $Py @('-m', 'pip', 'install', '-q', '--upgrade', 'pip')
if ($code -ne 0) { Die 'Failed to update pip' }

Say 'Dependencies'
# llama-cpp-python has no Windows wheel on PyPI. The index the maintainer keeps
# separately has one, and a Vulkan build too. The reason it is not written into
# requirements.txt is that this index is only needed on Windows.
# For cuda the maintainer's index is named cu124.
$llamaFlavor = if ($Backend -eq 'cuda') { 'cu124' } else { $Backend }
$llamaIndex = "https://abetlen.github.io/llama-cpp-python/whl/$llamaFlavor"
$code = Invoke-Native $Py @('-m', 'pip', 'install', '--extra-index-url', $llamaIndex,
                           '-r', (Join-Path $Here 'requirements.txt'))
if ($code -eq 0) {
  # yt-dlp alone is raised on its own. It is already in requirements, but -r
  # leaves what is installed alone, so re-running would not update it. When
  # YouTube changes its extraction path an old copy gets no formats at all
  # (issue #1), so this one line is what makes "re-install and it is fixed" hold.
  $code = Invoke-Native $Py @('-m', 'pip', 'install', '-q', '-U', 'yt-dlp[default]')
}
if ($code -ne 0) {
  Die @"
Installing the dependencies failed.
  If it could not find a llama-cpp-python wheel, try again with -Backend cpu.
  Index: $llamaIndex
"@
}
$ytv = (Get-Native $Py @('-m', 'yt_dlp', '--version')).Lines | Select-Object -First 1
Ok "Installed (yt-dlp $ytv)"

if ($Backend -eq 'cuda') {
  Say 'CUDA runtime (cudart · cuBLAS)'
  # llama.cpp's CUDA wheel calls cudart64_12.dll and cublas64_12.dll but does not carry
  # them. Pull them out of NVIDIA's PyPI runtime packages and put them in llama_cpp\lib --
  # llama_cpp puts that directory at the front of PATH and then opens the DLLs, so it finds
  # them there. The same 12.4.* as cu124.
  $code = Invoke-Native $Py @('-m', 'pip', 'install', '-q',
                             'nvidia-cuda-runtime-cu12==12.4.*', 'nvidia-cublas-cu12==12.4.*')
  if ($code -ne 0) { Die 'Failed to install the CUDA runtime packages. Try again with -Backend vulkan.' }
  $site = ((Get-Native $Py @('-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])')).Lines | Select-Object -First 1).Trim()
  # It does not ask by importing llama_cpp -- the CUDA wheel's llama.dll only opens once these DLLs are there.
  $libDir = Join-Path $site 'llama_cpp\lib'
  if (-not (Test-Path $libDir)) { Die "No llama_cpp\lib: $libDir" }
  foreach ($pair in @(@('cuda_runtime', 'cudart64_12.dll'), @('cublas', 'cublas64_12.dll'), @('cublas', 'cublasLt64_12.dll'))) {
    $src = Join-Path $site "nvidia\$($pair[0])\bin\$($pair[1])"
    if (-not (Test-Path $src)) { Die "No CUDA runtime DLL: $src" }
    Copy-Item -Force $src (Join-Path $libDir $pair[1])
  }
  Ok 'Put cudart64_12 · cublas64_12 · cublasLt64_12 into llama_cpp\lib'
}

# ---- 3. Transcription runtime ----------------------------------------------
Say 'Transcription runtime (transcribe.cpp)'
# macOS/Linux used to fetch the source here and build it with CMake, but the
# Windows wheel carries the CPU and Vulkan backends as DLLs. Fetch it, unpack it,
# done.
# If it is not installed yet a traceback comes out here. That is normal, and that
# is why Get-Native catches it -- called plainly, 5.1 turns this traceback into a
# terminating error and halts the whole install (issue #1).
if ((Get-Native $Py @('-c', 'import transcribe_cpp')).Code -eq 0) {
  Skip 'installed'
} else {
  $code = Invoke-Native $Py @('-m', 'pip', 'install', 'transcribe-cpp')
  if ($code -ne 0) { Die 'Failed to install transcribe-cpp' }
  Ok 'installed'
}
$backendProbe = @'
import transcribe_cpp
kinds = [b.kind for b in transcribe_cpp.backends()]
print('  Available backends: ' + ', '.join(sorted(set(kinds))))
if 'vulkan' not in kinds:
    print('  (No GPU path was picked up. Transcription runs on the CPU --')
    print('   updating the graphics driver may bring it up.)')
'@
# Failing to read the backends does not halt the install. Telling you is the point.
$probe = Invoke-PyFile $backendProbe
if ($probe.Code -eq 0) { $probe.Lines | ForEach-Object { Write-Host $_ } }
else { Skip 'Could not read the backend list (transcription runs on the CPU too)' }

# ---- 4. Models -------------------------------------------------------------
Say 'Models'
# The list lives in one place, modelhub.py -- the same table as "Engine management ›
# Models and tools" on the screen. What is already there is skipped, and what broke
# off (.part) resumes. This script used to hold its own list of addresses and fetch
# them with curl, which made two copies of the same thing as the screen.
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null
$env:MIMIWATCH_MODEL_DIR = $ModelDir
$mhArgs = @((Join-Path $Here 'modelhub.py'), 'download', 'default')
if ($WithGemma) { $mhArgs += '--with-gemma' }
$code = Invoke-Native $Py $mhArgs
if ($code -ne 0) { Die 'Could not fetch every model. Run it again and it resumes.' }

# ---- 5. Settings -----------------------------------------------------------
Say 'Settings'
$cfg = Join-Path $Here 'backends.json'
if (Test-Path $cfg) {
  Skip 'backends.json present (not overwritten)'
} else {
  Copy-Item (Join-Path $Here 'backends.example.json') $cfg
  Ok 'backends.json created'
}
New-Item -ItemType Directory -Force -Path (Join-Path $Here 'data') | Out-Null

# ---- 6. Check --------------------------------------------------------------
Say 'Check'
$env:MIMIWATCH_MODEL_DIR = $ModelDir
$verify = @'
import os, sys
sys.path.insert(0, sys.argv[1])
import stream, tcpp_asr                                   # noqa: F401
import modelhub
# No file names are baked in. The required models follow the default engines of the current settings.
ov = modelhub.overview()
missing = [i['label'] for i in ov['items']
           if i.get('required') and i['state'] not in ('ready', 'system')]
if missing:
    print('  missing: ' + ', '.join(missing))
    raise SystemExit(1)
print('  modules load OK')
print('  required models OK')
try:
    print('  ffmpeg:', stream.ffmpeg_cmd())
except FileNotFoundError:
    print('  no ffmpeg -- winget install --id Gyan.FFmpeg, or fetch it from "Engine management > Models and tools" on the screen')
'@
$check = Invoke-PyFile $verify @($Here)
$check.Lines | ForEach-Object { Write-Host $_ }
if ($check.Code -ne 0) { Die 'Install check failed' }
Ok 'Install check passed'

Write-Host "`nInstallation finished.`n" -ForegroundColor White
Write-Host '  Run:    .\run.ps1'
Write-Host '  Screen: http://localhost:8900'
if ($ModelDir -notmatch [regex]::Escape('mimiwatch\models')) {
  Write-Host "`n  The models are somewhere other than the default location. Give the same value when you run it:"
  Write-Host "    .\run.ps1 -ModelDir '$ModelDir'"
}
Write-Host "`nFor how to use it see README.md, and for the Windows side docs/WINDOWS.md."
