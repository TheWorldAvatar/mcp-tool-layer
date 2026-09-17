@echo off
setlocal
cd /d "%~dp0"
if defined TWA_LOCKED_PYTHON (
  set "LOCKED_PY=%TWA_LOCKED_PYTHON%"
) else if exist "%USERPROFILE%\AppData\Local\anaconda3\envs\mcp_layer\python.exe" (
  set "LOCKED_PY=%USERPROFILE%\AppData\Local\anaconda3\envs\mcp_layer\python.exe"
) else (
  set "LOCKED_PY=%~dp0.venv\Scripts\python.exe"
)
if not exist "%LOCKED_PY%" (
  echo [FAIL] Locked Python is missing: %LOCKED_PY%
  echo        Official s1-s4 used conda env mcp_layer, else run setup.cmd with Python 3.11.
  exit /b 1
)
echo [OK] Locked Python %LOCKED_PY%
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
"%LOCKED_PY%" "%~dp0scripts\run_locked.py" %*
exit /b %ERRORLEVEL%
