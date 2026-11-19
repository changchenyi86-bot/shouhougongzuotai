@echo off
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\python -m wecom_agent calibrate
pause
