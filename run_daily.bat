@echo off
chcp 65001 >nul
REM 以脚本自身所在目录为工作目录，避免任务计划里 cd 失败后在调用者 cwd 执行
cd /d "%~dp0"
py -3.12 -c "from reporter import send_morning_report; send_morning_report()"
