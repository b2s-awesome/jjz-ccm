@echo off
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || goto :err
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt || goto :err
)
.venv\Scripts\python main.py
goto :eof
:err
echo Setup failed. Install Python 3.10+ from python.org (tick "Add to PATH") and retry.
pause
