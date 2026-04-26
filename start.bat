@echo off
setlocal EnableExtensions EnableDelayedExpansion
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

if /i "%choice%"=="2" goto terminal_mode
goto web_mode

:terminal_mode
python main.py
goto done

:web_mode
set "DASHBOARD_URL=http://127.0.0.1:5000"
set "DASHBOARD_PORT=5000"
call :find_chrome

echo.
call :is_dashboard_running
if defined DASHBOARD_RUNNING goto dashboard_already_running

echo   Starting web dashboard runtime...
start "Polymarket Web Dashboard Runtime" cmd /k "cd /d ""%~dp0"" && python web.py"
echo   Waiting for dashboard to become ready...
call :wait_for_dashboard
if defined DASHBOARD_RUNNING goto open_dashboard_main

echo   [WARN] Dashboard did not report ready yet.
echo   Check the runtime window for startup errors, then open %DASHBOARD_URL%.
echo.
goto open_dashboard_main

:dashboard_already_running
echo   Dashboard is already running on port %DASHBOARD_PORT% ^(PID !DASHBOARD_PID!^).
echo   Not starting another web.py process.
echo.
goto open_dashboard_main

:open_dashboard_main
call :open_dashboard
goto done

:done
pause
exit /b 0

:find_chrome
set "CHROME_EXE="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
goto :eof

:is_dashboard_running
set "DASHBOARD_RUNNING="
set "DASHBOARD_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%DASHBOARD_PORT% .*LISTENING"') do (
    set "DASHBOARD_RUNNING=1"
    set "DASHBOARD_PID=%%P"
    goto :eof
)
goto :eof

:wait_for_dashboard
set "DASHBOARD_RUNNING="
set "DASHBOARD_PID="
for /l %%I in (1,1,30) do (
    call :is_dashboard_running
    if defined DASHBOARD_RUNNING goto :eof
    timeout /t 1 /nobreak >nul
)
goto :eof

:open_dashboard
echo   Opening web dashboard...
if defined CHROME_EXE (
    echo   Opening dashboard in Chrome...
    start "" "%CHROME_EXE%" "%DASHBOARD_URL%"
) else (
    echo   Chrome not found, falling back to your default browser...
    start "" "%DASHBOARD_URL%"
)
echo   Open %DASHBOARD_URL% in your browser if it does not open automatically.
echo.
goto :eof
