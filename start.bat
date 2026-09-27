@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [DeployPanel] 正在启动本地服务，浏览器将自动打开 http://127.0.0.1:8787
start "" http://127.0.0.1:8787
python server.py