[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 8765, [switch]$StartModel)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $labRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/setup_lab.ps1 first.' }
if ($StartModel) { & (Join-Path $PSScriptRoot 'start_local_model.ps1') }
$serviceUrl = "http://127.0.0.1:$Port"
try {
    $health = Invoke-RestMethod -Uri "$serviceUrl/api/v1/health" -TimeoutSec 2
    if ($health.scope -eq 'planner_only') { Write-Output "Workbench is already running at $serviceUrl"; return }
} catch { }
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) { throw "Port $Port is occupied; choose a different -Port explicitly." }
$logDir = Join-Path $labRoot ('data/live_runs/workbench-service-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $logDir | Out-Null
$process = Start-Process -FilePath $python -ArgumentList @('-X','utf8','scripts/lab.py','serve','--port',"$Port") -WorkingDirectory $labRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'stdout.log') -RedirectStandardError (Join-Path $logDir 'stderr.log')
@{ process_id = $process.Id; url = $serviceUrl; launched_at = [DateTimeOffset]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDir 'service.json') -Encoding UTF8
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "$serviceUrl/api/v1/health" -TimeoutSec 1
        if ($health.scope -eq 'planner_only') { Write-Output "Workbench is ready at $serviceUrl (PID $($process.Id)). Logs: $logDir"; return }
    } catch { Start-Sleep -Milliseconds 500 }
    if ($process.HasExited) { throw "Workbench stopped during startup. Inspect $logDir/stderr.log" }
}
throw "Workbench did not become ready. Inspect $logDir/stderr.log"
