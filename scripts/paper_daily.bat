@echo off
REM Paper Trading Daily Wrapper
REM 由 Windows Task Scheduler 调用, 隔离路径空格 + Git Bash 转义问题
cd /d "D:\Claude Code\workspace\projects\stock-scanner"
"C:\Users\12739\AppData\Local\Programs\Python\Python312\python.exe" scripts\paper_vs_backtest.py --mode daily >> logs\paper_daily_%date:~0,4%%date:~5,2%%date:~8,2%.log 2>&1
