@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv is missing. Double-click install.bat first.
  pause
  exit /b 1
)

if not defined MT4_AGENT_DATA_DIR (
  if defined LOCALAPPDATA (
    set "MT4_AGENT_DATA_DIR=%LOCALAPPDATA%\RakutenMT4Agent"
  ) else (
    set "MT4_AGENT_DATA_DIR=%~dp0data"
  )
)

set "PYTHONUTF8=1"
echo Rakuten MT4 Windows Acceptance Test Runner
echo Data directory: %MT4_AGENT_DATA_DIR%
echo Starting the existing Web Admin and opening the Windows Test page.
echo If the Agent is already running, open the Windows Test tab there instead.
echo.
".venv\Scripts\python.exe" -m app.testing --data-dir "%MT4_AGENT_DATA_DIR%" --open-web
if errorlevel 1 (
  echo.
  echo [ERROR] Acceptance Test Runner stopped with an error.
  pause
  exit /b 1
)
pause
