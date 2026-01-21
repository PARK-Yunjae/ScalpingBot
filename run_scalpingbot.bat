@echo off
cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

if not exist logs mkdir logs

python -m scalping.engine.scalp_engine

pause
