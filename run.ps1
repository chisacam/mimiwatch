#Requires -Version 5.1
<#
.SYNOPSIS
  Starts the mimiwatch server. The Windows counterpart of run.sh.

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
  Write-Host 'There is no virtual environment. Run .\install.ps1 first.' -ForegroundColor Red
  exit 1
}
if ($ModelDir) { $env:MIMIWATCH_MODEL_DIR = $ModelDir }

# The server finds its own files by relative path, so it runs from the repository.
Push-Location $Here
try {
  & $Py 'server.py' '--port' $Port
} finally {
  Pop-Location
}
