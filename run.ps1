#Requires -Version 5.1
<#
.SYNOPSIS
  mimiwatch 서버를 띄웁니다. run.sh의 윈도우판입니다.

.EXAMPLE
  .\run.ps1
  .\run.ps1 -Port 8951
  .\run.ps1 -ModelDir 'D:\models'
#>
[CmdletBinding()]
param(
  [int] $Port = 8900,
  [string] $ModelDir
)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Here '.venv\Scripts\python.exe'

if (-not (Test-Path $Py)) {
  Write-Host '가상환경이 없습니다. 먼저 .\install.ps1 을 실행하십시오.' -ForegroundColor Red
  exit 1
}
if ($ModelDir) { $env:MIMIWATCH_MODEL_DIR = $ModelDir }

# 서버가 자기 파일을 상대 경로로 찾으므로 저장소에서 실행합니다.
Push-Location $Here
try {
  & $Py 'server.py' '--port' $Port
} finally {
  Pop-Location
}
