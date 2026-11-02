@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_LAUNCH="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_LAUNCH=py -3"
if not defined PYTHON_LAUNCH (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_LAUNCH=python"
)
if not defined PYTHON_LAUNCH (
    echo يلزم Python 3.11 أو أحدث. ثبّت Python ثم أعد المحاولة.
    exit /b 2
)
%PYTHON_LAUNCH% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo Tkinter/Tcl-Tk غير متوفر. أعد تثبيت Python مع خيار Tcl/Tk ثم أعد setup.bat.
    exit /b 2
)

if not exist ".venv\Scripts\python.exe" (
    %PYTHON_LAUNCH% -m venv .venv
    if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 exit /b 1
if not exist "config.ini" copy /y "config.ini.example" "config.ini" >nul
if errorlevel 1 exit /b 1

echo اكتمل التثبيت. أضف ملف Google OAuth، ثم شغّل run_gui.bat مرة واحدة لتسجيل الدخول. بعد ذلك شغّل run_automation.bat --preflight.
exit /b 0
