@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -m blackfong_ecosystem.cli %*
    exit /b %ERRORLEVEL%
)

if exist "%SCRIPT_DIR%.venv-codex\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv-codex\Scripts\python.exe" -m blackfong_ecosystem.cli %*
    exit /b %ERRORLEVEL%
)

if exist "%SCRIPT_DIR%.venv311-codex\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv311-codex\Scripts\python.exe" -m blackfong_ecosystem.cli %*
    exit /b %ERRORLEVEL%
)

python -m blackfong_ecosystem.cli %*
