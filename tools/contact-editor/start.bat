@echo off
setlocal
cd /d "%~dp0"

set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo A working Python 3 installation is required.
    pause
    exit /b 1
)

start "" "http://127.0.0.1:8766/"
%PY% server.py %*
