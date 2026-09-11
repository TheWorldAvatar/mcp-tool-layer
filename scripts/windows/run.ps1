#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "[FAIL] .venv is missing. Run setup.cmd first." -ForegroundColor Red
    exit 1
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$forward = @($args)
if ($forward.Count -ge 1 -and $forward[0] -match '^\d+$') {
    $forward = @("--cases") + $forward
}
& $Python (Join-Path $RepoRoot "scripts\run_default_pipeline.py") @forward
exit $LASTEXITCODE
