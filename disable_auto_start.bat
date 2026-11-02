@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0disable_auto_start.ps1"
exit /b %ERRORLEVEL%
