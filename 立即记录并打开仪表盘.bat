@echo off
cd /d "%~dp0"
if exist "bin\collector.exe" cd bin
if not exist data mkdir data
type nul> data\flush.request
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8787/dashboard.html"
echo Requested immediate record, opened dashboard.
pause
