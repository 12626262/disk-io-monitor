@echo off
cd /d "%~dp0"
if exist "data\collector.pid" (
    for /f %%i in (data\collector.pid) do taskkill /PID %%i /F >nul 2>&1
    del /q "data\collector.pid" >nul 2>&1
)
if exist "data\serve.pid" (
    for /f %%i in (data\serve.pid) do taskkill /PID %%i /F >nul 2>&1
    del /q "data\serve.pid" >nul 2>&1
)
echo Monitor stopped. Data is kept in data\disk_io.db
pause
