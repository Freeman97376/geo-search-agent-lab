[CmdletBinding()]
param([switch]$ValidateOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
$infraRoot = Join-Path $labRoot 'infra/searxng'
$lock = Get-Content -LiteralPath (Join-Path $infraRoot 'images.lock.json') -Raw | ConvertFrom-Json
foreach ($entry in @($lock.searxng, $lock.valkey)) {
    if ($entry.image -notmatch '^docker\.io/[a-z0-9/_-]+@sha256:[a-f0-9]{64}$') {
        throw 'A verified official image digest is required. No latest fallback is allowed.'
    }
}
$envFile = Join-Path $infraRoot '.env'
$envText = "SEARXNG_IMAGE=$($lock.searxng.image)`nVALKEY_IMAGE=$($lock.valkey.image)`n"
if ((Test-Path -LiteralPath $envFile) -and ((Get-Content -LiteralPath $envFile -Raw).Trim() -ne $envText.Trim())) {
    throw 'Existing Compose .env differs from image lock; review it before changing the baseline.'
}
[IO.File]::WriteAllText($envFile, $envText, (New-Object Text.UTF8Encoding $false))
$configDir = Join-Path $infraRoot 'core-config'
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$settings = Join-Path $configDir 'settings.yml'
if (-not (Test-Path -LiteralPath $settings)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $secret = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    $template = Get-Content -LiteralPath (Join-Path $infraRoot 'settings.template.yml') -Raw
    [IO.File]::WriteAllText($settings, $template.Replace('__GENERATE_LOCAL_SECRET__', $secret), (New-Object Text.UTF8Encoding $false))
}
$compose = @('compose', '-p', 'geo-search-lab-searxng', '--project-directory', $infraRoot, '--env-file', $envFile, '-f', (Join-Path $infraRoot 'compose.yaml'))
$resolvedJson = & docker @compose config --format json
if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed.' }
$resolved = ($resolvedJson -join "`n") | ConvertFrom-Json
if ($resolved.services.core.image -ne $lock.searxng.image -or $resolved.services.valkey.image -ne $lock.valkey.image) {
    throw 'Effective Compose image differs from lock (possibly an ambient environment override).'
}
$bindings = @($resolved.services.core.ports)
if ($bindings.Count -ne 1 -or $bindings[0].host_ip -ne '127.0.0.1' -or [string]$bindings[0].published -ne '8888' -or $bindings[0].target -ne 8080) {
    throw 'Effective Compose binding must be exactly 127.0.0.1:8888:8080.'
}
if ($ValidateOnly) { Write-Output 'Compose configuration is valid; no containers started.'; return }
# Bound daemon detection so a broken Desktop cannot hang the startup script.
$engineProbe = Start-Job { docker info --format '{{.ServerVersion}}' 2>&1; if ($LASTEXITCODE -ne 0) { throw 'Docker engine unavailable' } }
try {
    $done = Wait-Job $engineProbe -Timeout 20
    if (-not $done -or $engineProbe.State -ne 'Completed') { throw 'Docker engine unavailable. Start or repair Docker Desktop, then retry; no backend substitution was made.' }
    Receive-Job $engineProbe | Out-Null
} finally { Stop-Job $engineProbe -ErrorAction SilentlyContinue; Remove-Job $engineProbe -Force }
& docker @compose up -d
if ($LASTEXITCODE -ne 0) { throw 'SearXNG startup failed; inspect Compose logs.' }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8888/healthz' -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { throw 'SearXNG did not become healthy. Inspect docker compose logs.' }
Write-Output 'SearXNG is ready at http://127.0.0.1:8888/. Engine availability is not yet verified; run scripts/probe_searxng.py.'
