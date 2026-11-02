@echo off
setlocal
cd /d "%~dp0"

echo =====================================================================
echo  Automatic Lyrics Synchronizer - Launching Web Studio (Port 8585)
echo =====================================================================
echo [INFO] Working Directory: %CD%

echo [INFO] Starting FastAPI server on http://127.0.0.1:8585 ...
start "" "http://127.0.0.1:8585/sync"
python -m uvicorn server.main:app --host 127.0.0.1 --port 8585 --reload
