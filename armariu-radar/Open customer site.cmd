@echo off
rem Double-click to open the Armariu customer site (read-only) in your browser. Keep this window open while you use it.
rem It rebuilds the site's data from your local database first, which takes about 30 seconds.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
"%PY%" -m radar.portal --rebuild
pause
