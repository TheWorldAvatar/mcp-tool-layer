@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [FAIL] .venv is missing. Run setup.cmd first.
  exit /b 1
)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\run_default_pipeline.py" %*
exit /b %ERRORLEVEL%
