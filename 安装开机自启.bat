@echo off
cd /d "%~dp0"
schtasks /Create /F /TN "DiskIOCollector" /SC ONLOGON /RL LIMITED /TR "wscript.exe \"%CD%\start_hidden.vbs\""
echo Autostart installed (task: DiskIOCollector).
pause
