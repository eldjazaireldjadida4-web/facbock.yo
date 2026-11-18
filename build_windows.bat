@echo off
REM Build the facbock.yo one-dir bundle on Windows with PyInstaller.
REM Run from the repo root:  build_windows.bat
REM Output: dist\facbock-yo\  (facbock-yo.exe, facbock-yo-doctor.exe,
REM facbock-yo-automation.exe). The Windows installer is compiled
REM separately with Inno Setup: installer\facbock-yo.iss
REM
REM This file is stored with LF line endings in git (see .gitattributes):
REM keep it LF when editing so the snapshot push stays clean.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_LAUNCH="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_LAUNCH=py -3"
if not defined PYTHON_LAUNCH (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_LAUNCH=python"
)
if not defined PYTHON_LAUNCH (
    echo Python 3.11 or newer is required. Install Python, then retry.
    exit /b 2
)

if not exist ".venv-build\Scripts\python.exe" (
    %PYTHON_LAUNCH% -m venv .venv-build
    if errorlevel 1 exit /b 1
)
.venv-build\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1
.venv-build\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 exit /b 1
.venv-build\Scripts\python.exe -m pip install pyinstaller
if errorlevel 1 exit /b 1

.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm facbock-yo.spec
if errorlevel 1 exit /b 1

REM PyInstaller 6 puts bundled data files under dist\facbock-yo\_internal\.
REM The installer and the user need the example configs beside the .exe,
REM so copy them to the bundle root as well.
copy /y config.ini.example dist\facbock-yo\config.ini.example >nul
if errorlevel 1 exit /b 1
copy /y rights_manifest.example.json dist\facbock-yo\rights_manifest.example.json >nul
if errorlevel 1 exit /b 1

echo.
echo Bundle built: dist\facbock-yo\
dir dist\facbock-yo\facbock-yo.exe
echo.
echo Smoke test:
dist\facbock-yo\facbock-yo.exe --version
if errorlevel 1 exit /b 1
dist\facbock-yo\facbock-yo.exe --help >nul
if errorlevel 1 exit /b 1
echo --help OK. Now run the installer compiler, if Inno Setup is installed:
echo   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\facbock-yo.iss
exit /b 0
