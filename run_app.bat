@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  Options Trading Assistant - launcher
REM  Double-click this file to start the app. A browser tab
REM  opens automatically. Keep the black window open while you
REM  use the app; close it to stop.
REM ============================================================

REM First run only: build the environment if it is missing.
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup - creating the environment and installing packages...
  echo This happens once and may take a couple of minutes.
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  echo Setup done.
  echo.
)

echo Starting your Options Trading Assistant...
echo.
echo   It will open at:  http://localhost:8501
echo   Keep THIS window open while you use the app.
echo   To stop the app, close this window.
echo.

".venv\Scripts\python.exe" -m streamlit run app.py ^
  --server.port 8501 ^
  --server.headless false ^
  --server.runOnSave true ^
  --browser.gatherUsageStats false

echo.
echo The app has stopped. Press any key to close this window.
pause >nul
