@echo off
title game_bot GUI
rem 一键启动 GUI（双击即用）：自动请求管理员权限，已有实例自动结束后重启
cd /d "%~dp0"

rem ── 自动提权（GUI 必须管理员运行，否则发给游戏的点击会被系统静默丢弃）──
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在请求管理员权限，请在弹窗中点击「是」...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set PYTHONUTF8=1
set PY=C:\Users\言\AppData\Local\Programs\Python\Python39\python.exe
if not exist "%PY%" set PY=python

rem ── 结束旧实例：读锁文件 temp\bot.lock 里的 PID，精准 taskkill ──
if exist "temp\bot.lock" (
    for /f %%p in (temp\bot.lock) do (
        echo 结束旧实例 PID=%%p
        taskkill /F /PID %%p >nul 2>&1
    )
    del /q temp\bot.lock >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

echo 正在启动 GUI...
"%PY%" "%~dp0gui.py"
echo.
echo === 程序已退出。如有报错见上方。按任意键关闭窗口 ===
pause >nul
