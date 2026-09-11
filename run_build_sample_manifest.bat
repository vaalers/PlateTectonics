@echo off
setlocal

REM Usage:
REM   run_build_sample_manifest.bat <layout_root> <analysis_root> [output_xlsx]
REM   run_build_sample_manifest.bat "D:\PhenixData\PlateLayouts" "D:\PhenixData\Analyzed" "manifest\sample_manifest.xlsx"

set "LAYOUT_ROOT="
set "ANALYSIS_ROOT="
set "OUTPUT_PATH=manifest\sample_manifest.xlsx"
set "ENV_NAME=pt-py310"

if not "%~1"=="" set "LAYOUT_ROOT=%~1"
if not "%~2"=="" set "ANALYSIS_ROOT=%~2"
if not "%~3"=="" set "OUTPUT_PATH=%~3"

if "%LAYOUT_ROOT%"=="" (
  echo [ERROR] Usage: run_build_sample_manifest.bat ^<layout_root^> ^<analysis_root^> [output_xlsx]
  exit /b 1
)
if "%ANALYSIS_ROOT%"=="" (
  echo [ERROR] Usage: run_build_sample_manifest.bat ^<layout_root^> ^<analysis_root^> [output_xlsx]
  exit /b 1
)

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
  echo [ERROR] Open Anaconda PowerShell or set CONDA_EXE, then retry.
  exit /b 1
)

echo [INFO] Using conda: %CONDA_CMD%
echo [INFO] Layout root: %LAYOUT_ROOT%
echo [INFO] Analysis root: %ANALYSIS_ROOT%
echo [INFO] Output: %OUTPUT_PATH%

set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
"%CONDA_CMD%" run -n "%ENV_NAME%" python -m pt.build_plate_manifest --layout-root "%LAYOUT_ROOT%" --analysis-root "%ANALYSIS_ROOT%" --output "%OUTPUT_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo [ERROR] Manifest build failed with exit code %EXIT_CODE%.
  exit /b %EXIT_CODE%
)

echo [INFO] Manifest build complete.
exit /b 0
