@echo off
chcp 65001 >nul
title r1999 挂机
cd /d E:\m\game_bot
echo === r1999 挂机启动(独立进程,不受对话会话影响) ===
echo 日志: temp\r1999_loop.log  急停: 关闭本窗口或 Ctrl+C
python -u main.py --game r1999
pause
