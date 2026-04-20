@echo off
setlocal
title Polymarket Copy Trading Bot
cd /d "%~dp0"

echo.
echo  ============================================
echo   Polymarket Copy Trading Bot
echo  ============================================
echo.
echo   [1] Web dashboard mode
echo   [2] Terminal mode
echo.
if not defined choice set /p choice="  Choose mode [1]: "
set "choice=%choice: =%"
set "choice=%choice:~0,1%"
if "%choice%"=="" set "choice=1"

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not on PATH.
    echo  Install Python from https://www.python.org/downloads/
    pause
    exit /b 1
)

python -c "import flask_socketio" >nul 2>&1
if errorlevel 1 (
    echo  Installing dependencies from requirements.txt...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo  [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
    echo.
)

if /i "%choice%"=="2" (
    python main.py
) else (
    echo.
    echo   Starting web dashboard...
    echo   Open http://localhost:5000 in your browser if it does not open automatically.
    echo.
    start "" http://localhost:5000
    python web.py
)

pause
