@echo off
title Polymarket 跟单机器人
cd /d "%~dp0"

echo.
echo  ============================================
echo   Polymarket 体育跟单机器人
echo  ============================================
echo.
echo   [1] 网页面板 (推荐，自动打开浏览器)
echo   [2] 终端模式 (命令行)
echo.
set /p choice="  请选择 [1]: "
if "%choice%"=="" set choice=1

python --version >nul 2>&1
if errorlevel 1 (
    echo  [错误] 未找到 Python！请先安装 python.org
    pause
    exit /b 1
)

python -c "import flask_socketio" >nul 2>&1
if errorlevel 1 (
    echo  正在安装依赖...
    pip install -r requirements.txt -q
    echo  安装完成！
    echo.
)

if "%choice%"=="2" (
    python main.py
) else (
    echo.
    echo   正在启动...
    echo   浏览器将自动打开 http://localhost:5000
    echo   每 15 秒自动扫描一次，防止漏单
    echo.
    start http://localhost:5000
    python web.py
)
pause
