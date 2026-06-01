@echo off
chcp 65001 >nul
:: 停止后台运行的算力老板(Web)
echo 正在停止算力老板…
for /f "tokens=2 delims=," %%p in ('wmic process where "name='python.exe' and commandline like '%%boss.py%%'" get processid /format:csv 2^>nul ^| findstr [0-9]') do (
  taskkill /pid %%p /f >nul 2>&1 && echo 已停止 PID %%p
)
echo 完成。
timeout /t 2 >nul
