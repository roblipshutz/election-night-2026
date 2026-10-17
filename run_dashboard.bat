@echo off
REM Starts the election-night fetcher + local web server, then opens the
REM dashboard full screen in Microsoft Edge. Close this window to stop.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set PY=.venv\Scripts\python.exe) else (set PY=python)
start "" msedge --kiosk "http://localhost:8000/web/" --edge-kiosk-type=fullscreen
%PY% app\election_night.py
pause
