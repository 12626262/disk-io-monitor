@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist data mkdir data
if not exist output mkdir output

rem Single-instance check: do not start a second monitor
if exist "data\collector.pid" (
    set /p OLD_PID=<data\collector.pid
    tasklist /FI "PID eq !OLD_PID!" 2>nul | findstr /i "collector" >nul
    if not errorlevel 1 (
        echo.
        echo [Info] Monitor is already running. PID=!OLD_PID! Nothing to do.
        echo To restart it, stop the current monitor first, then run this script again.
        echo.
        pause
        exit /b 0
    )
    del /q "data\collector.pid" >nul 2>&1
)

if exist "bin\collector.exe" (
    start "" /min bin\collector.exe
    start "" /min bin\serve.exe
) else (
    start "" /min pythonw collector.py
    start "" /min pythonw serve.py
)
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8787/dashboard.html"
echo.
echo Monitor started. Dashboard: http://127.0.0.1:8787/dashboard.html
pause
