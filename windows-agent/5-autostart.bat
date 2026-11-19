@echo off
chcp 65001 >nul
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
(
echo @echo off
echo chcp 65001 ^>nul
echo cd /d "%~dp0"
echo start "" /min "%~dp04-start.bat"
) > "%STARTUP%\wecom-agent.bat"
echo 已设置开机自动启动发送程序。要取消，删除这个文件即可：
echo   %STARTUP%\wecom-agent.bat
pause
