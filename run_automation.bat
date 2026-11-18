@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo شغّل setup.bat أولاً.
  pause
  exit /b 2
)
if /I "%~1"=="--preflight" goto preflight
.venv\Scripts\python.exe fb_youtube_uploader_v49_pro.py --cli %*
if errorlevel 1 (
  echo.
  echo انتهى التشغيل برمز خطأ %ERRORLEVEL%. راجع الرسالة أعلاه.
  pause
)
exit /b %ERRORLEVEL%
:preflight
.venv\Scripts\python.exe fb_youtube_uploader_v49_pro.py --preflight
if errorlevel 1 pause
exit /b %ERRORLEVEL%
