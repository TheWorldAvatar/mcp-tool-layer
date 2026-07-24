@echo off
REM MOP Extraction Agent - Web Extension Startup Script (Windows)

echo ========================================================================
echo MOP Extraction Agent - Web Extension
echo Web Application Startup
echo ========================================================================
echo.

REM Check if we're in the right directory
if not exist "app.py" (
    echo Error: app.py not found!
    echo Please run this script from the mini_marie\webapp directory
    pause
    exit /b 1
)

REM Check if .env file exists
if not exist ".env" (
    echo Warning: .env file not found
    echo Please create a .env file with your API key:
    echo   copy env.example .env
    echo   REM Then edit .env and add your OPENAI_API_KEY
    echo.
    set /p continue="Do you want to continue anyway? (y/n) "
    if /i not "%continue%"=="y" exit /b 1
) else (
    echo Found .env file
)

REM Check Python version
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set python_version=%%i
echo Python version: %python_version%

REM Check if dependencies are installed
echo.
echo Checking dependencies...
python -c "import flask" 2>nul
if errorlevel 1 (
    echo Flask not found. Installing dependencies...
    pip install -r requirements.txt
) else (
    echo Flask installed
)

REM Navigate to project root for proper imports
cd ..\..
set PYTHONPATH=%CD%

echo.
echo Starting MOP Extraction Agent web extension...
echo URL: http://127.0.0.1:5000
echo Press Ctrl+C to stop
echo.
echo ========================================================================
echo.

REM Run the app
python mini_marie\webapp\app.py --host 127.0.0.1 --port 5000 --debug

pause

