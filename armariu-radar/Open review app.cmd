@echo off
rem Double-click to open the Armariu review app in your browser. Keep this window open while you use it.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
"%PY%" -m radar.ui
pause
