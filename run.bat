@echo off
cd /d "%~dp0"
title LapVoice
if not exist ".venv\Scripts\python.exe" (
    echo Setup has not been run yet. Please double-click setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m iracing_coach %*
echo.
pause
