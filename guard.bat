@echo off
rem ===== DeployPanel Guard =====
rem Check if port 8787 is listening; if not, start server.py with Python311 (hidden window)
set "DP=C:\Users\s\Doubao\chats\2026-09-26\new-chat-2\deploy-panel"
set "PY=C:\Users\s\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%DP%\server.py" exit /b 0
netstat -ano | findstr ":8787" | findstr "LISTENING" >nul
if %errorlevel%==0 exit /b 0
start "" "%PY%" "%DP%\server.py"
exit /b 0
