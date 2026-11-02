@echo off
setlocal
cd /d "%~dp0"

echo =====================================================================
echo  Automatic Lyrics Synchronizer - Setup (Global Python)
echo =====================================================================
echo [INFO] Working Directory: %CD%

REM 1. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in your PATH.
    echo Please install Python 3.13+ from https://www.python.org/
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do (
    set "PY_VER=%%i"
)
echo [INFO] Detected Python version: %PY_VER%

REM 2. Upgrade pip (Global)
echo [INFO] Upgrading pip globally...
python -m pip install --upgrade pip

REM 3. Install dependencies globally
echo [INFO] Installing required dependencies globally from requirements.txt...
python -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)

REM 4. GPU & PyTorch CUDA Inspection
echo [INFO] Checking for NVIDIA GPU acceleration...
where nvidia-smi >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] NVIDIA GPU detected.
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
    echo.
    echo [TIP] To enable fast CUDA GPU acceleration for Whisper ASR:
    echo Run: pip install --index-url https://download.pytorch.org/whl/cu121 torch torchvision
) else (
    echo [NOTE] No NVIDIA GPU detected. Whisper will run in CPU mode.
)

echo.
echo =====================================================================
echo  Setup Completed Successfully! Launching Web Studio...
echo =====================================================================
echo.
call "%~dp0run.bat"
