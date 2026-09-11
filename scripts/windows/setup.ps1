#Requires -Version 5.1
<#
.SYNOPSIS
  Create a local Python 3.11+ venv and install this repository for the shipped Windows pipeline.

.DESCRIPTION
  Assumes Python is already installed. Put .env next to pyproject.toml first
  (REMOTE_BASE_URL and REMOTE_API_KEY). Then run setup.cmd from the repo root.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-PythonCommand {
    $candidates = @(
        @("py", "-3.13"),
        @("py", "-3.12"),
        @("py", "-3.11"),
        @("py", "-3"),
        @("python"),
        @("python3")
    )
    foreach ($item in $candidates) {
        $exe = $item[0]
        $exeArgs = @()
        if ($item.Count -gt 1) {
            $exeArgs = $item[1..($item.Count - 1)]
        }
        $probe = $exeArgs + @("-c", "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}'); raise SystemExit(0 if v>=(3,11) else 2)")
        try {
            $output = & $exe @probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $output) {
                $version = ($output | Select-Object -Last 1).Trim()
                return @{ Exe = $exe; Args = $exeArgs; Version = $version }
            }
        } catch {
            continue
        }
    }
    return $null
}

Write-Host "Repository: $RepoRoot"

$python = Get-PythonCommand
if ($null -eq $python) {
    Write-Host "[FAIL] Python 3.11+ was not found. Install Python 3.11 or newer and re-run setup.cmd." -ForegroundColor Red
    exit 1
}
$pythonLabel = "$($python.Exe) $($python.Args -join ' ')".Trim()
Write-Host "[OK] Python $($python.Version) ($pythonLabel)"

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "Creating .venv"
    $venvArgs = $python.Args + @("-m", "venv", ".venv")
    & $python.Exe @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Host "[FAIL] Could not create .venv" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[OK] Reusing .venv"
}

Write-Step "Installing dependencies"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $venvPython -m pip install -r (Join-Path $RepoRoot "requirements-runtime.txt")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $venvPython -m pip install -e $RepoRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Step "Checking .env"
$envPath = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $RepoRoot ".env.example") $envPath
    Write-Host "[WARN] Created .env from .env.example. Fill REMOTE_API_KEY and REMOTE_BASE_URL before run.cmd." -ForegroundColor Yellow
} else {
    Write-Host "[OK] .env is present"
}

$envText = Get-Content $envPath -Raw -ErrorAction SilentlyContinue
foreach ($key in @("REMOTE_BASE_URL", "REMOTE_API_KEY")) {
    if ($envText -notmatch "(?m)^$key=.+") {
        Write-Host "[WARN] $key is empty in .env" -ForegroundColor Yellow
    }
}

Write-Step "Dataset folders"
$mopsDir = Join-Path $RepoRoot "scenarios\mops\datasets\eval30"
$medDir = Join-Path $RepoRoot "scenarios\medical\datasets\eval30"
New-Item -ItemType Directory -Force -Path $mopsDir | Out-Null
New-Item -ItemType Directory -Force -Path $medDir | Out-Null

function Count-MainPdfs([string]$Folder) {
    @(Get-ChildItem -LiteralPath $Folder -Filter *.pdf -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "*_si.pdf" }).Count
}

$mopsCount = Count-MainPdfs $mopsDir
$medCount = @(Get-ChildItem -LiteralPath $medDir -Filter *.pdf -ErrorAction SilentlyContinue).Count
Write-Host "[OK] Chemistry PDFs: $mopsCount / 30 in $mopsDir"
Write-Host "[OK] OntoMed PDFs:    $medCount / 30 in $medDir"
if ($mopsCount -lt 10 -or $medCount -lt 10) {
    Write-Host "[WARN] Default run.cmd uses 10 cases. Copy eval30 PDFs into those folders (chemistry names use underscores in the DOI, e.g. 10.1002_anie.201811027.pdf)." -ForegroundColor Yellow
}

Write-Step "Scorer engines"
$env:PYTHONPATH = $RepoRoot
& $venvPython -m src.kg_building.scorer_repo
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] Scoring engines were not cloned. run.cmd will retry. Gold files live in data\scorer_assets." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next:"
Write-Host "  1. Confirm .env has REMOTE_BASE_URL and REMOTE_API_KEY"
Write-Host "  2. Confirm eval30 PDFs are in the dataset folders above"
Write-Host "  3. run.cmd          (10 cases)"
Write-Host "     run.cmd 20       (20 cases, max 30)"
Write-Host "     run.cmd --from-step extract"
exit 0
