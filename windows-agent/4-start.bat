@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 企业微信发送程序
:loop
.venv\Scripts\python -m wecom_agent run
echo 发送程序退出了，10 秒后自动重启。要停止请直接关闭这个窗口。
timeout /t 10 >nul
goto :loop
