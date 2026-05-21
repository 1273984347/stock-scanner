@echo off
chcp 65001 >nul
echo ========================================
echo   TDX 日K线数据更新
echo ========================================
echo.

cd /d "%~dp0.."
echo 当前目录: %CD%
echo.

python scripts/update_tdx_data.py --workers 8

echo.
echo 按任意键退出...
pause >nul
