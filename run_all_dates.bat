@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  run_all_dates.bat
REM  Runs pt-run sequentially for every 6-digit date folder
REM  found directly inside the given root folder.
REM
REM  Usage:
REM    run_all_dates.bat "D:\PhenixData\Analyzed"
REM    run_all_dates.bat "D:\PhenixData\Analyzed" --no-phenix --min-n 18
REM ============================================================

if "%~1"=="" (
    echo [ERROR] No root folder provided.
    echo Usage: run_all_dates.bat ^<root_folder^> [extra pt-run args...]
    exit /b 1
)

set "ROOT=%~1"

REM Collect any extra args (everything after the first argument)
set "EXTRA="
:collect_args
shift
if "%~1"=="" goto :done_args
set "EXTRA=!EXTRA! "%~1""
goto :collect_args
:done_args

set "ENV_NAME=pt-py310"

REM Locate conda
set "CONDA_CMD="
if defined CONDA_EXE set "CONDA_CMD=%CONDA_EXE%"
if not defined CONDA_CMD if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\miniconda3\Scripts\conda.exe"
if not defined CONDA_CMD if exist "%LOCALAPPDATA%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%LOCALAPPDATA%\miniconda3\Scripts\conda.exe"
if not defined CONDA_CMD if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\anaconda3\Scripts\conda.exe"
if not defined CONDA_CMD if exist "%ProgramData%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%ProgramData%\miniconda3\Scripts\conda.exe"
if not defined CONDA_CMD (
    for %%I in (conda.exe) do set "CONDA_CMD=%%~$PATH:I"
)
if not defined CONDA_CMD (
    echo [ERROR] Could not find conda.exe.
    echo         Open Anaconda PowerShell or set CONDA_EXE, then retry.
    exit /b 1
)

if not exist "%ROOT%" (
    echo [ERROR] Root folder does not exist: %ROOT%
    exit /b 1
)

echo [INFO] Root folder : %ROOT%
echo [INFO] Extra args  : %EXTRA%
echo [INFO] Conda       : %CONDA_CMD%
echo [INFO] Environment : %ENV_NAME%
echo.

set "PROCESSED=0"
set "ERRORS=0"
set "FAILED_DATES="

for /d %%D in ("%ROOT%\*") do (
    set "DNAME=%%~nxD"
    REM Check if folder name is exactly 6 digits (MMDDYY)
    echo !DNAME! | findstr /r "^[0-9][0-9][0-9][0-9][0-9][0-9]$" >nul 2>&1
    if !errorlevel!==0 (
        echo ========================================
        echo [INFO] Starting date: !DNAME!
        echo ========================================
        "%CONDA_CMD%" run -n "%ENV_NAME%" pt-run "%%D" !EXTRA!
        set "RC=!errorlevel!"
        if !RC! neq 0 (
            echo [ERROR] Pipeline FAILED for !DNAME! ^(exit code !RC!^)
            set /a ERRORS+=1
            set "FAILED_DATES=!FAILED_DATES! !DNAME!"
        ) else (
            echo [OK]    Finished: !DNAME!
        )
        set /a PROCESSED+=1
        echo.
    )
)

echo ========================================
echo [INFO] Batch complete.
echo        Dates processed : !PROCESSED!
echo        Errors          : !ERRORS!
if !ERRORS! gtr 0 (
    echo        Failed dates    :!FAILED_DATES!
)
echo ========================================

if !ERRORS! gtr 0 exit /b 1
exit /b 0
