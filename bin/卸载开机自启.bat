@echo off
schtasks /Delete /F /TN "DiskIOCollector"
echo Autostart removed.
pause
