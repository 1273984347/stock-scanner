@echo off
REM Paper Trading Daily Wrapper
REM 由 Windows Task Scheduler 调用, 隔离路径空格 + Git Bash 转义问题
REM 回到仓库根（本文件在 scripts/ 下），解释器用 py launcher，避免硬编码用户 profile
cd /d "%~dp0.."
py -3.12 scripts\paper_vs_backtest.py --mode daily >> logs\paper_daily_%date:~0,4%%date:~5,2%%date:~8,2%.log 2>&1
