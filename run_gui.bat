@echo off
setlocal
REM Launch the PTHTS graphical interface in the pt-py310 conda environment.
REM Usage: run_gui.bat [streamlit options, e.g. --server.port 8502]

set "ENV_NAME=pt-py310"
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
  echo [ERROR] Could not find conda.exe. Open Anaconda Prompt or set CONDA_EXE, then retry.
  exit /b 1
)

echo [INFO] Starting PTHTS GUI (environment %ENV_NAME%). Close this window to stop it.
"%CONDA_CMD%" run -n "%ENV_NAME%" --no-capture-output pt-gui %*
