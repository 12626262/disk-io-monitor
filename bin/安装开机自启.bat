@echo off
cd /d "%~dp0"
schtasks /Create /F /TN "DiskIOCollector" /SC ONLOGON /RL LIMITED /TR "\"%CD%\collector.exe\""
echo Autostart installed (task: DiskIOCollector).
pause
