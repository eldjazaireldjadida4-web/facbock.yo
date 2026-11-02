@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo شغّل setup.bat أولاً.
    exit /b 2
)
.venv\Scripts\python.exe fb_youtube_uploader_v49_pro.py %*
exit /b %errorlevel%
