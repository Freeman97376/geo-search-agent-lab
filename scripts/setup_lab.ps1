[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
Push-Location $labRoot
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        & uv venv --python 3.12.13 .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    }
    & uv pip sync --python .venv/Scripts/python.exe --require-hashes requirements-upstream.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    & uv pip check --python .venv/Scripts/python.exe
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    Write-Output 'Isolated dependencies are ready. Restore pinned upstreams with scripts/restore_upstreams.py if missing.'
} finally { Pop-Location }
