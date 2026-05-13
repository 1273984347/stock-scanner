@echo off
chcp 65001 >nul
cd /d "D:\Claude Code\workspace\projects\stock-scanner"
python run.py >> run.log 2>&1
