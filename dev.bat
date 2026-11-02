@echo off
setlocal
cd /d "%~dp0"

rem usage: dev.bat [project_folder]     (default: game)
set "PROJECT=%~1"
if "%PROJECT%"=="" set "PROJECT=game"
set MISSING=0

echo ============================================================
echo   GEL dev environment  -  project: %PROJECT%
echo ============================================================
echo Checking requirements...

call :need "%PROJECT%\game.gel"                        "your project's main source file"
call :need "compiler\build.py"                         "GEL build script"
call :need "compiler\gel_codegen.py"                   "GEL code generator"
call :need "compiler\gel_parser.py"                    "GEL parser"
call :need "compiler\snes_gel.lark"                    "GEL grammar"
call :need "compiler\png_to_snes_tiles.py"             "PNG asset converter"
call :need "compiler\stdlib\snes.gel"                  "GEL standard library"
call :need "tools\asar\asar64.dll"                     "Asar assembler DLL, 64-bit"
call :need "tools\asar\bindings\python\asar.py"        "Asar Python binding"
call :need "tools\mesen\Mesen.exe"                     "Mesen emulator - download Mesen 2 from https://www.mesen.ca and put Mesen.exe in tools\mesen\ - it is not stored in git"
call :need "tools\mesen_gel_hook.lua"                  "Mesen hot-reload script"
call :need "tools\gel_bridge.py"                       "hot-reload file watcher"

rem Pick an interpreter that actually runs; the Windows Store "python" alias can exist but be a broken stub.
set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo   [MISSING] A working Python 3 ^(not found as "py" or "python"^)  -  https://www.python.org/downloads/
    set /a MISSING+=1
) else (
    for %%M in (lark:lark watchdog:watchdog PIL:pillow) do for /f "tokens=1,2 delims=:" %%A in ("%%M") do (
        %PY% -c "import %%A" >nul 2>&1
        if errorlevel 1 (
            echo   [MISSING] Python package '%%B'  ^(fix: %PY% -m pip install %%B^)
            set /a MISSING+=1
        )
    )
)

if %MISSING% GTR 0 (
    echo.
    echo %MISSING% requirement^(s^) missing. Fix the above and run dev.bat again.
    pause
    exit /b 1
)
echo All requirements found.
echo.

echo Building initial ROM...
%PY% compiler\build.py "%PROJECT%"
if errorlevel 1 (
    echo Initial build failed. Fix the errors above, then run dev.bat again.
    pause
    exit /b 1
)

echo Launching Mesen with hot-reload hook...
start "" "%~dp0tools\mesen\Mesen.exe" "%~dp0%PROJECT%\build\game.sfc" "%~dp0%PROJECT%\build\gel_hook.lua"

echo Starting bridge ^(Ctrl+C to stop^)...
%PY% tools\gel_bridge.py "%PROJECT%"
exit /b 0

:need
if not exist "%~1" (
    echo   [MISSING] %~1  -  %~2
    set /a MISSING+=1
)
exit /b 0
