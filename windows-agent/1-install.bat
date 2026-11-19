@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   企业微信发送程序 - 安装
echo ============================================
set "PY="
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if defined PY goto :venv
echo 没有找到 Python 3.12，正在尝试自动安装……
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if defined PY goto :venv
echo.
echo 自动安装没有成功，请手动安装 Python 3.12：
echo   1. 打开 https://www.python.org/downloads/windows/
echo   2. 找到 Python 3.12 的 Windows installer 64-bit，下载并安装
echo   3. 安装第一页记得勾选 Add python.exe to PATH
echo 装好后重新双击运行本文件。
pause
exit /b 1

:venv
if exist .venv\Scripts\python.exe goto :deps
%PY% -m venv .venv
if errorlevel 1 goto :fail

:deps
echo 正在安装依赖，第一次大约需要几分钟……
set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple"
.venv\Scripts\python -m pip install -q --upgrade pip %MIRROR%
.venv\Scripts\python -m pip install -r requirements.txt %MIRROR%
if errorlevel 1 .venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 goto :fail
echo.
.venv\Scripts\python -m wecom_agent setup
pause
exit /b 0

:fail
echo 安装失败，请把上面的报错截图发给开发人员。
pause
exit /b 1
