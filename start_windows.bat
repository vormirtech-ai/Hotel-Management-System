@echo off
REM Hotel Management System - one-click start for Windows
cd /d "%~dp0"
if not exist .venv (
  echo Creating Python environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Installing / checking requirements...
python -m pip install --disable-pip-version-check -q -r requirements.txt
start "" http://localhost:8000
python run.py
pause
