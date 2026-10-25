# Restore an isolated local runtime without modifying .venv or an existing .env.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $env:UV_CACHE_DIR = Join-Path $projectRoot '.validation/uv-cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.validation/python'
    # Copies avoid cache hardlink dependencies in a synced Windows workspace.
    $env:UV_LINK_MODE = 'copy'
    & uv python install 3.12 --no-bin --no-registry
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
    $environmentPath = Join-Path $projectRoot '.venv-review'
    $pythonPath = Join-Path $environmentPath 'Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $environmentPath)) {
        & uv venv --python 3.12 $environmentPath
        if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed.' }
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Existing .venv-review is incomplete. Inspect it before replacing it.'
    }
    & $pythonPath --version
    if ($LASTEXITCODE -ne 0) { throw 'Existing .venv-review interpreter is unusable.' }
    & uv pip install --python $pythonPath -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & $pythonPath scripts/check_dependencies.py
    if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
    Write-Output 'PASS: local environment ready. Follow docs/RUNBOOK.md for model and HTTP acceptance.'
} finally {
    Pop-Location
}
