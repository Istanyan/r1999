@echo off
setlocal
chcp 65001 >nul
title r1999 挂机
cd /d "%~dp0"

set "PYEXE="
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    echo [ERROR] 没有找到 Python。
    echo 先运行 setup_r1999_env.bat，或安装 Python 3.9 x64 并勾选 Add python.exe to PATH。
    pause
    exit /b 1
)

echo ==========================================
echo  r1999 挂机启动
echo  项目目录: %CD%
echo  Python: %PYEXE%
echo  日志目录: data\logs
echo  急停: 关闭本窗口或 Ctrl+C
echo ==========================================
echo.

"%PYEXE%" r1999_portable_launcher.py
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] r1999 退出码 %RC%，请查看上面的报错或 data\logs\r1999_loop.log。
    pause
)
exit /b %RC%
