$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot

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

# The shell serves the built frontend from its own backend process. A missing
# dist is a build away; a stale one is worse - it silently shows yesterday's
# app (once the popup window loaded a bundle built before the popup route
# existed and the whole chat page rendered inside it). Any source newer than
# the build therefore triggers a rebuild here. Plugin frontends are folded
# into the main bundle by import.meta.glob, so their sources count too;
# plugin backend files (plugin/<id>/plugin.py) do not.
$dist = Join-Path $projectRoot "frontend\dist\index.html"
$sourceRoots = @(
    "frontend\src",
    "frontend\index.html",
    "frontend\vite.config.ts",
    "frontend\package.json"
) | ForEach-Object { Join-Path $projectRoot $_ } | Where-Object { Test-Path $_ }
$pluginFrontends = Get-ChildItem (Join-Path $projectRoot "plugin") -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Join-Path $_.FullName "frontend" } |
    Where-Object { Test-Path $_ }
$sourceRoots += $pluginFrontends

$build = $false
if (-not (Test-Path $dist)) {
    Write-Host "frontend/dist 不存在，先构建前端："
    $build = $true
} else {
    $distTime = (Get-Item $dist).LastWriteTime
    $newest = Get-ChildItem -Recurse -File $sourceRoots |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -ne $newest -and $newest.LastWriteTime -gt $distTime) {
        Write-Host "frontend/dist 落后于源码（最近改动：$($newest.FullName)），重新构建前端："
        $build = $true
    }
}
if ($build) {
    Push-Location (Join-Path $projectRoot "frontend")
    try { npm run build } finally { Pop-Location }
}

# Dev against the vite server instead:  $env:JARVIS_SHELL_URL = "http://127.0.0.1:5173"
Push-Location (Join-Path $projectRoot "backend")
try {
    uv sync --group desktop
    uv run python -m app.desktop
}
finally {
    Pop-Location
}
