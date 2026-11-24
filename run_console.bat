@echo off
REM Minwon console - local server (data/ is read with fetch, so file:// does not work)
cd /d "%~dp0"
start "" http://localhost:8000/index.html#analysis
python -m http.server 8000
if errorlevel 1 py -m http.server 8000
pause
