@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo Rakuten MT4 Remote Login Agent V1 - first-time setup
echo ====================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python Launcher ^(py.exe^) was not found.
  echo Install Python 3.11+ from https://www.python.org/downloads/windows/
  echo Enable the Python Launcher and try again.
  pause
  exit /b 1
)

py -3 -V
py -3 -c "import sys; raise SystemExit(0 if sys.version_info ^>= (3,11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.11+ is required ^(py -3 did not pass^).
  echo Install Python 3.11 or newer and run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create .venv.
    pause
    exit /b 1
  )
)

echo Installing auditable Python dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :install_failed
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :install_failed
".venv\Scripts\python.exe" -m app.selfcheck
if errorlevel 1 goto :install_failed
".venv\Scripts\python.exe" -V

echo.
echo Installation complete.
echo Double-click start.bat to launch the Agent and Web Admin.
echo Web Admin (default): http://127.0.0.1:8765
echo.
pause
exit /b 0

:install_failed
echo.
echo [ERROR] Installation failed. Review the message above.
pause
exit /b 1
