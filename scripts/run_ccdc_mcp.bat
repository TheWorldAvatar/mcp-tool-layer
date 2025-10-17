@echo off
REM Launch the CCDC MCP server (Windows only)
REM Usage: double-click or run from cmd: scripts\run_ccdc_mcp.bat
REM Reads environment from .env (optional) or system env.

SETLOCAL ENABLEDELAYEDEXPANSION

REM Load .env if present (simple parser: KEY=VALUE, no quotes, no spaces)
IF EXIST .env (
  FOR /F "usebackq tokens=1,* delims==" %%A IN (".env") DO (
    IF NOT "%%A"=="" SET %%A=%%B
  )
)

REM Respect CSD_CONDA_ENV or default to csd311
IF NOT DEFINED CSD_CONDA_ENV SET CSD_CONDA_ENV=csd311

REM Respect CONDA_EXE path or default to conda
IF NOT DEFINED CONDA_EXE SET CONDA_EXE=conda

"%CONDA_EXE%" run -n "%CSD_CONDA_ENV%" python -m src.mcp_servers.ccdc.main
IF %ERRORLEVEL% EQU 0 GOTO :eof

CALL conda activate "%CSD_CONDA_ENV%"
python -m src.mcp_servers.ccdc.main

ENDLOCAL