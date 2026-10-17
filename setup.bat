@echo off
REM One-time setup: creates a private Python environment and installs requests.
cd /d "%~dp0"
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo Setup done. Double-click run_dashboard.bat to start.
pause
