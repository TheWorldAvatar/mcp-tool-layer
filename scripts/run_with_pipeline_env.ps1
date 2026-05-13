# Run Python using the repo-local pipeline virtual environment (.venv-pipeline).
# Example:
#   .\scripts\run_with_pipeline_env.ps1 generic_main.py --config configs/pipeline_ontomop_backtest.json --input-dir raw_data_mop --hash 88c21a74 --test
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PythonArgs
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repoRoot ".venv-pipeline\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "Missing $py. Create it with Python 3.12 and install requirements-pipeline-win.txt (see README or repo docs)."
    exit 1
}
Push-Location $repoRoot
try {
    & $py @PythonArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
