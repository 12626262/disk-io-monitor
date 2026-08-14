@echo off
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges to stop the monitor...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
cd /d "%~dp0"
taskkill /IM collector.exe /T /F
taskkill /IM serve.exe /T /F
taskkill /IM filewatch.exe /T /F
echo Monitor stopped. Data is kept in data\disk_io.db
pause