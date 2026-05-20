@echo off
cd /d "%~dp0"
title Area Scanner Demo Launcher

echo ========================================
echo Area Scanner + Unity Demo Launcher
echo ========================================
echo.

python --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found.
    echo Please install Python or make sure python is added to PATH.
    pause
    exit /b 1
)

echo [1/3] Checking Python packages...
python -c "import PySide6, pyqtgraph, OpenGL, numpy, serial" >nul 2>nul
if errorlevel 1 (
    echo [2/3] Missing packages. Installing requirements.txt...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Package installation failed.
        echo Please check your network connection or pip setup.
        pause
        exit /b 1
    )
) else (
    echo [2/3] Packages are ready.
)

echo [3/3] Starting Python GUI...
echo.
echo Unity should listen on UDP port 5055.
echo Python Unity Output is enabled by default.
echo You can use Replay first to test the Unity scene.
echo.

python main.py

pause
