@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [FAIL] .venv is missing. Run setup.cmd first.
  exit /b 1
)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\eval_inputs.py" check
  exit /b %ERRORLEVEL%
)
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\eval_inputs.py" %*
exit /b %ERRORLEVEL%
