$ErrorActionPreference = 'Stop'
$infraRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'infra/searxng'
docker compose -p geo-search-lab-searxng --project-directory $infraRoot --env-file (Join-Path $infraRoot '.env') -f (Join-Path $infraRoot 'compose.yaml') stop
if ($LASTEXITCODE -ne 0) { throw 'Stopping lab containers failed.' }
