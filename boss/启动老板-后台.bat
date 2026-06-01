@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在无窗口后台启动算力老板…
powershell -NoProfile -WindowStyle Hidden -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'boss\.py' } | ForEach-Object { try{Stop-Process -Id $_.ProcessId -Force}catch{} }; Start-Sleep 1; Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList 'boss.py' -WindowStyle Hidden -RedirectStandardOutput 'boss.log' -RedirectStandardError 'boss.err'"
echo.
echo 已在后台运行(无窗口)。控制台: http://127.0.0.1:8000
echo 日志: boss.log   停止: 双击 停止老板.bat
timeout /t 3 >nul
