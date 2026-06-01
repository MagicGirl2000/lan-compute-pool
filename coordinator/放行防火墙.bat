@echo off
chcp 65001 >nul
:: 放行局域网算力池端口（协调端9000 / 老板8000）。必须【右键→以管理员身份运行】。
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo [!] 请右键本文件，选择「以管理员身份运行」。
  pause
  exit /b
)
echo 正在放行 9000 / 8000 入站...
netsh advfirewall firewall delete rule name="LAN-Compute 9000" >nul 2>&1
netsh advfirewall firewall delete rule name="LAN-Compute 8000" >nul 2>&1
netsh advfirewall firewall add rule name="LAN-Compute 9000" dir=in action=allow protocol=TCP localport=9000
netsh advfirewall firewall add rule name="LAN-Compute 8000" dir=in action=allow protocol=TCP localport=8000
echo.
echo 完成。现在手机填 http://10.167.10.45:9000 即可连入。
pause
