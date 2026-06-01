' 无窗口后台启动算力老板(Web 控制台)。双击本文件即可。
' 用隐藏窗口跑 python.exe（保留隐藏控制台，multiprocessing 才稳定），日志写 boss.log。
' 控制台访问： http://127.0.0.1:8000   停止： 双击 停止老板.bat
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
' 0 = 隐藏窗口；False = 不等待
sh.Run "cmd /c .venv\Scripts\python.exe boss.py > boss.log 2>&1", 0, False
