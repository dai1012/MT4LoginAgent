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
".venv\Scripts\python.exe" -c "import app.main" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] The local Python environment is invalid. Run install.bat again.
  pause
  exit /b 1
)
echo Rakuten MT4 Remote Login Agent V1
echo Web Admin (default): http://127.0.0.1:8765
echo Data directory: %MT4_AGENT_DATA_DIR%
echo Log file: %MT4_AGENT_DATA_DIR%\logs\agent.log
echo The Local admin token will be printed below; keep it private.
echo Press Ctrl+C to stop. MT4 terminals already running are not terminated.
echo.
".venv\Scripts\python.exe" -m app.main --data-dir "%MT4_AGENT_DATA_DIR%"
if errorlevel 1 (
  echo.
  echo [ERROR] Agent stopped with an error.
  pause
)
