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
    set "DASHBOARD_URL=http://localhost:5000"
    set "CHROME_EXE="
    if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not defined CHROME_EXE if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    if not defined CHROME_EXE if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

    echo.
    echo   Starting web dashboard...
    if defined CHROME_EXE (
        echo   Opening dashboard in Chrome...
        start "" "%CHROME_EXE%" "%DASHBOARD_URL%"
    ) else (
        echo   Chrome not found, falling back to your default browser...
        start "" "%DASHBOARD_URL%"
    )
    echo   Open %DASHBOARD_URL% in your browser if it does not open automatically.
    echo.
    python web.py
)

pause
