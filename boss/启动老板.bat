@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   算力老板 Compute Boss  启动中…
echo ============================================================

rem ── 先杀掉已在运行的老板实例（避免多实例抢 8000 端口，和协调端同坑）──
for /f "tokens=2 delims=," %%p in ('wmic process where "name='python.exe' and commandline like '%%boss.py%%'" get processid /format:csv 2^>nul ^| findstr [0-9]') do (
  echo 关闭旧实例 PID %%p
  taskkill /pid %%p /f >nul 2>&1
)

set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
  echo [!] 未找到 venv，正在用系统 Python 创建…
  python -m venv .venv
)
echo 安装/校验依赖…
"%PY%" -m pip install -q -r requirements.txt

echo.
echo 控制台将开在 http://127.0.0.1:8000
echo （手机算力 app 仍连「协调端」:9000；老板在协调端之上做编排）
echo.
"%PY%" boss.py
pause
