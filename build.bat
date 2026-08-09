@echo off
rem Build with conda env disk-io-monitor (Python 3.8, Win7+ compatible)
cd /d "%~dp0"
set PY=G:\Conda\Anaconda\envs\disk-io-monitor\python.exe
if not exist bin mkdir bin
set DLLS=--add-binary "G:\Conda\Anaconda\envs\disk-io-monitor\Library\bin\sqlite3.dll;." --add-binary "G:\Conda\Anaconda\envs\disk-io-monitor\Library\bin\libcrypto-3-x64.dll;." --add-binary "G:\Conda\Anaconda\envs\disk-io-monitor\Library\bin\libssl-3-x64.dll;."
"%PY%" -m PyInstaller --onefile --noconsole --name collector --distpath bin --workpath build --specpath build %DLLS% collector.py
"%PY%" -m PyInstaller --onefile --noconsole --name serve --distpath bin --workpath build --specpath build %DLLS% serve.py
"%PY%" -m PyInstaller --onefile --console --name report --distpath bin --workpath build --specpath build %DLLS% report.py
echo.
echo Build finished. Output: bin\
