@echo off
setlocal
cd /d "%~dp0"
title LapVoice - Setup

rem Optional: setup.bat qwen2.5:7b  (pick a different model than the default)
set "MODEL=%~1"
if "%MODEL%"=="" set "MODEL=llama3.2:3b"

echo.
echo === LapVoice setup ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python was not found.
    echo     Install Python 3.10 or newer from https://www.python.org/downloads/
    echo     IMPORTANT: tick "Add python.exe to PATH" on the first installer screen,
    echo     then run this setup again.
    pause
    exit /b 1
)

echo [1/3] Creating a private Python environment...
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" goto venvfail

echo [2/3] Installing required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail

where ollama >nul 2>nul
if errorlevel 1 goto noollama

echo [3/3] Downloading the AI model %MODEL% - one time only, a few minutes...
ollama pull %MODEL%
if errorlevel 1 goto pullfail

echo.
echo Testing your setup...
".venv\Scripts\python.exe" -m iracing_coach --check --model %MODEL%

echo.
echo ============================================================
echo  Setup complete. Start iRacing, get in the car, then
echo  double-click run.bat
if /i not "%MODEL%"=="llama3.2:3b" echo  NOTE: add  model: "%MODEL%"  under ollama: in config.local.yaml
echo ============================================================
pause
exit /b 0

:venvfail
echo [X] Could not create the Python environment.
pause
exit /b 1

:pipfail
echo [X] Installing packages failed. Check your internet connection and try again.
pause
exit /b 1

:noollama
echo.
echo [!] Ollama was not found. Packages are installed, but you still need the AI engine:
echo     1. Install Ollama from https://ollama.com/download
echo     2. Run setup.bat again
echo     Or skip the AI model entirely: run.bat --no-llm   gives simple built-in tips.
pause
exit /b 1

:pullfail
echo [X] Downloading the model failed. Check your internet connection and try again.
pause
exit /b 1
