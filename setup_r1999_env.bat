@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title r1999 环境安装 / 检查
cd /d "%~dp0"

set "CHECK_ONLY=0"
if /I "%~1"=="--check" set "CHECK_ONLY=1"

echo ==========================================
echo  r1999 环境安装
echo  项目目录: %CD%
echo ==========================================
echo.

call :find_python
if errorlevel 1 goto :fail
echo [OK] Python: %PY_CMD%
%PY_CMD% --version

set "VPY=.venv\Scripts\python.exe"

if "%CHECK_ONLY%"=="1" goto :check

if exist "%VPY%" (
    echo [OK] 已存在 .venv
) else (
    echo [..] 创建 .venv
    %PY_CMD% -m venv .venv
    if errorlevel 1 goto :fail
)
if not exist "%VPY%" (
    echo [ERROR] .venv 创建失败
    goto :fail
)

echo [..] 升级 pip
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo [..] 安装 requirements.txt
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

call :ensure_adb
goto :finish

:check
if exist "%VPY%" (
    echo [OK] 已存在 .venv
    echo [..] 检查依赖
    "%VPY%" -m pip check
) else (
    echo [WARN] 没有 .venv；去掉 --check 运行本脚本可创建
)
call :check_adb
goto :finish

:finish
echo.
echo ==========================================
echo  完成
echo  启动: 双击 启动r1999挂机.bat
echo  自检: "%VPY%" r1999_portable_launcher.py --self-test --ocr-smoke
echo  日志: data\logs
echo ==========================================
if "%CHECK_ONLY%"=="0" pause
exit /b 0

:fail
echo.
echo [FAILED] 环境没有装完，请根据上面的报错修复后重跑。
pause
exit /b 1

:find_python
set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 (
    py -3.9 -c "import sys; raise SystemExit(0 if sys.maxsize > 2**32 and (3,9) <= sys.version_info[:2] < (3,13) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=py -3.9"
        exit /b 0
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.maxsize > 2**32 and (3,9) <= sys.version_info[:2] < (3,13) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=python"
        exit /b 0
    )
)
echo [ERROR] 没有找到 64 位 Python 3.9-3.12。
echo 请安装 Python 3.9 x64，并勾选 Add python.exe to PATH：
echo https://www.python.org/downloads/release/python-3913/
echo 或执行: winget install -e --id Python.Python.3.9
exit /b 1

:check_adb
if exist "adb\adb.exe" (
    echo [OK] ADB: adb\adb.exe
    exit /b 0
)
where adb >nul 2>nul
if not errorlevel 1 (
    echo [OK] ADB: 已在 PATH 中
    exit /b 0
)
echo [WARN] 没有找到 ADB；运行本脚本不带 --check 会自动尝试下载
exit /b 0

:ensure_adb
if exist "adb\adb.exe" (
    echo [OK] ADB 已存在: adb\adb.exe
    exit /b 0
)
where adb >nul 2>nul
if not errorlevel 1 (
    echo [OK] 使用 PATH 中的 ADB
    exit /b 0
)
echo [..] 下载 Android platform-tools
if not exist "tools" mkdir "tools"
where curl >nul 2>nul
if errorlevel 1 (
    echo [WARN] 没有 curl，跳过 ADB 自动下载；请手动放到 adb\ 或安装 platform-tools。
    exit /b 0
)
curl.exe -L --fail --retry 2 --connect-timeout 20 -o "tools\platform-tools.zip" "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
if errorlevel 1 (
    echo [WARN] ADB 下载失败；请用 MuMu 自带 adb，或设置 GAMEBOT_ADB。
    exit /b 0
)
where tar >nul 2>nul
if errorlevel 1 (
    echo [WARN] 没有 tar，无法解压 platform-tools.zip；请手动解压。
    exit /b 0
)
tar -xf "tools\platform-tools.zip" -C "tools"
if errorlevel 1 (
    echo [WARN] platform-tools 解压失败。
    exit /b 0
)
if not exist "adb" mkdir "adb"
for %%F in (adb.exe AdbWinApi.dll AdbWinUsbApi.dll) do (
    if exist "tools\platform-tools\%%F" copy /Y "tools\platform-tools\%%F" "adb\%%F" >nul
)
if exist "adb\adb.exe" (
    echo [OK] ADB 已放到 adb\adb.exe
) else (
    echo [WARN] ADB 文件不完整；请手动检查 adb\。
)
exit /b 0
