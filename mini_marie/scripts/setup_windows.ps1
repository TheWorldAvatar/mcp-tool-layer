# Windows first-time setup (PowerShell) — run from project root
# Usage: .\mini_marie\scripts\setup_windows.ps1
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location $ProjectRoot
Write-Host "Project root: $ProjectRoot"

$python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
& $python --version

$venv = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $venv)) {
    & $python -m venv $venv
}
& "$venv\Scripts\python.exe" -m pip install --upgrade pip wheel
& "$venv\Scripts\pip.exe" install -r requirements-mini-marie.txt

if ($env:INSTALL_GUI -eq "1") {
    & "$venv\Scripts\pip.exe" install -r requirements-gui.txt
}
if ($env:INSTALL_KGQA -eq "1") {
    & "$venv\Scripts\pip.exe" install -r requirements-kgqa.txt
}

$env:PYTHONPATH = $ProjectRoot
& "$venv\Scripts\python.exe" -c "from mini_marie.cache_paths import ensure_runtime_dirs; ensure_runtime_dirs(); print('Runtime dirs OK')"

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example"
}

Write-Host @"

Setup complete.
  cd $ProjectRoot
  `$env:PYTHONPATH = '$ProjectRoot'
  .\.venv\Scripts\Activate.ps1
  python -m mini_marie.test_row_filters

"@
