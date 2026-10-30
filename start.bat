@echo off
setlocal
cd /d "%~dp0"
if exist "runtime\python.exe" (
  set "PY=runtime\python.exe"
) else (
  set "PY=python"
)
echo Starting image batch tool with %PY% ...
"%PY%" server.py
pause
