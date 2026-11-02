@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check --upgrade pip
if errorlevel 1 exit /b %errorlevel%
.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt
if errorlevel 1 exit /b %errorlevel%
.venv\Scripts\python.exe -m pytest %*
exit /b %errorlevel%
