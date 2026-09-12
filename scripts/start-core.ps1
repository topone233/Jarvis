$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
Push-Location (Join-Path $projectRoot "backend")
try {
    uv sync --group dev
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
}
finally {
    Pop-Location
}

