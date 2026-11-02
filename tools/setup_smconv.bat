@echo off
rem Builds smconv (the SNESMOD soundbank converter) from the bundled source into tools\smconv\smconv.exe.
rem Needs Go 1.23+ (https://go.dev/dl/). Only required when a project has sounds in sfx\ or audio\.
setlocal
cd /d "%~dp0\.."

where go >nul 2>&1
if errorlevel 1 (
    echo Go was not found. Install it from https://go.dev/dl/ and run this script again.
    exit /b 1
)
if not exist "tools\snesmod-main\smconv\main.go" (
    echo tools\snesmod-main\smconv is missing. Re-clone the repository.
    exit /b 1
)
if not exist "tools\smconv" mkdir "tools\smconv"

pushd "tools\snesmod-main\smconv"
go build -o "..\..\smconv\smconv.exe" .
set RC=%errorlevel%
popd

if %RC% NEQ 0 (
    echo smconv build failed.
    exit /b %RC%
)
echo Built tools\smconv\smconv.exe
exit /b 0
