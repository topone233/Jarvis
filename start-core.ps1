$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Plugin frontends (plugin/*/frontend) import the packages installed under
# frontend/node_modules, but node module resolution from plugin/ walks up to
# the project root, not into frontend/. A junction at the root closes that
# gap - machine-local, so it is recreated here whenever it is missing.
$junction = Join-Path $projectRoot "node_modules"
$target = Join-Path $projectRoot "frontend\node_modules"
if (-not (Test-Path $junction)) {
    if (-not (Test-Path $target)) {
        Push-Location (Join-Path $projectRoot "frontend")
        try { npm install } finally { Pop-Location }
    }
    New-Item -ItemType Junction -Path $junction -Target $target | Out-Null
}

Push-Location (Join-Path $projectRoot "backend")
try {
    uv sync --group dev
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
}
finally {
    Pop-Location
}

