[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
$profile = Get-Content -LiteralPath (Join-Path $labRoot 'geo_search_lab/config/planner_local.json') -Raw | ConvertFrom-Json
$serviceUrl = 'http://127.0.0.1:11434'
$running = $false
try {
    $null = Invoke-RestMethod -Uri "$serviceUrl/api/version" -TimeoutSec 2
    $running = $true
} catch { }
if (-not $running) {
    $ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if (-not $ollama) { throw 'Ollama is missing. Install it before starting this local profile.' }
    $listeners = @(Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) { throw 'Port 11434 is occupied by an unverified service.' }
    $logDir = Join-Path $labRoot ('data/live_runs/ollama-service-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $logDir | Out-Null
    $settings = @{ OLLAMA_HOST = '127.0.0.1:11434'; OLLAMA_NO_CLOUD = '1'; OLLAMA_CONTEXT_LENGTH = '8192'; OLLAMA_NUM_PARALLEL = '1'; OLLAMA_KEEP_ALIVE = '5m' }
    $previous = @{}
    try {
        foreach ($name in $settings.Keys) {
            $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
        }
        $process = Start-Process -FilePath $ollama.Source -ArgumentList 'serve' -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'stdout.log') -RedirectStandardError (Join-Path $logDir 'stderr.log')
        @{ process_id = $process.Id; launched_at = [DateTimeOffset]::UtcNow.ToString('o'); settings = $settings } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDir 'service.json') -Encoding UTF8
    } finally {
        foreach ($name in $settings.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try { $null = Invoke-RestMethod -Uri "$serviceUrl/api/version" -TimeoutSec 1; $running = $true; break } catch { Start-Sleep -Milliseconds 500 }
    }
}
if (-not $running) { throw 'Local model server did not become ready.' }
$listeners = @(Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)
if (@($listeners | Where-Object { $_.LocalAddress -notin @('127.0.0.1', '::1') }).Count -gt 0) { throw 'The existing service is not bound exclusively to loopback.' }
$tags = Invoke-RestMethod -Uri "$serviceUrl/api/tags" -TimeoutSec 5
$model = @($tags.models | Where-Object { $_.name -eq $profile.model -and $_.digest -eq $profile.model_digest })
if ($model.Count -ne 1) { throw 'Pinned Qwen model is missing or changed. This script does not silently download a replacement.' }
Write-Output 'Pinned Qwen is available at http://127.0.0.1:11434/v1. Existing service settings are not overwritten; record its context setting if started elsewhere.'
