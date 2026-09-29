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
  echo Install Python 3.12 ^(recommended^) from https://www.python.org/downloads/windows/
  echo Enable the Python Launcher and try again.
  pause
  exit /b 1
)

set "PY_CMD="
py -3.12 -V >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3.12"
if not defined PY_CMD (
  py -3.11 -V >nul 2>nul
  if not errorlevel 1 set "PY_CMD=py -3.11"
)
if not defined PY_CMD (
  echo [ERROR] Python 3.12 or 3.11 was not found.
  echo Install Python 3.12 ^(recommended^) or 3.11 from https://www.python.org/downloads/windows/
  echo The installer does not fall back to an arbitrary py -3 interpreter.
  pause
  exit /b 1
)
%PY_CMD% -V

if not exist ".venv\Scripts\python.exe" (
  echo Creating local virtual environment with %PY_CMD%...
  %PY_CMD% -m venv .venv
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
