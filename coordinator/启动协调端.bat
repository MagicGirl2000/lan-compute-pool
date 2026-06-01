@echo off
chcp 65001 >/dev/null
REM ============================================================
REM  启动分布式算力协调端（自动先杀掉所有旧实例，避免多进程抢端口）
REM  ★ 多个 coordinator.py 同时跑会抢 9000 端口 → 手机"时连时断"
REM ============================================================
echo 清理旧的 coordinator 实例...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*coordinator.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 >/dev/null
echo 启动协调端 :9000 ...
set CC_PORT=9000
start "" /min "%~dp0.venv\Scripts\python.exe" "%~dp0coordinator.py"
timeout /t 3 >/dev/null
echo.
echo 协调端已启动。看板: http://127.0.0.1:9000/dashboard
echo 手机 worker 连: 模拟器填 http://10.0.2.2:9000  真机填 http://本机局域网IP:9000
echo.
pause
