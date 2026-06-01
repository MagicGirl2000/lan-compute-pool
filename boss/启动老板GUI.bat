@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ── 先杀掉已在运行的 GUI 实例 ──
for /f "tokens=2 delims=," %%p in ('wmic process where "name='python.exe' and commandline like '%%boss_gui.py%%'" get processid /format:csv 2^>nul ^| findstr [0-9]') do (
  taskkill /pid %%p /f >nul 2>&1
)

set PY=.venv\Scripts\pythonw.exe
if not exist "%PY%" set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
  python -m venv .venv
  set PY=.venv\Scripts\python.exe
)
"%PY%" -m pip install -q -r requirements.txt
start "" "%PY%" boss_gui.py
