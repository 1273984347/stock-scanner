@echo off
chcp 65001 >nul
cd /d "D:\Claude Code\workspace\projects\stock-scanner"
python -c "from reporter import send_morning_report; send_morning_report()"
