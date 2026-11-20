# scripts/bootstrap_schedules.ps1 — 注册无人值守调度任务（评审 P0-4，2026-11-20）
#
# 背景：架构评审实测 `schtasks /query` 401 个任务 grep 零命中——README.md（TDX 数据更新·定时任务）、
# docs/PAPER_TRADING_GUIDE.md:46-53、task-scheduler-setup.md 三处文档声称的
# 计划任务**一台都没注册**，晨报 / paper 日报 / TDX 更新全部静默停摆（P0-3 场景 1）。
# 本脚本把三个每日任务一次性落到 Windows 任务计划（幂等：已存在先删再注册）。
#
# 执行（本脚本入库后**不自动运行**——注册计划任务属机器状态变更，由用户自行执行）：
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_schedules.ps1
# 移除全部任务：
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_schedules.ps1 -Remove
#
# 失败可见性：stdout/stderr 重定向到 logs\scheduled\<name>.log；任务本身是否按时跑
# 由推送心跳哨兵兜底（reporter/heartbeat.py 读 push_log + 交易日历，晨报自检）。

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$taskPath = "\StockScanner\"
$logDir = Join-Path $root "logs\scheduled"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$python = (Get-Command python).Source

function Remove-TaskIfExists([string]$name) {
    $existing = Get-ScheduledTask -TaskPath $taskPath -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskPath $taskPath -TaskName $name -Confirm:$false
        Write-Host "removed existing task: $taskPath$name"
    }
}

function Register-DailyTask([string]$name, [string]$time, [string]$exe, [string]$argStr) {
    Remove-TaskIfExists $name
    $action = New-ScheduledTaskAction -Execute $exe -Argument $argStr -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $time
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
    # S4U：不存密码、未登录也可跑；对外 HTTP（飞书）不受影响，仅 Windows 域内网络凭据受限
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskPath $taskPath -TaskName $name `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Host "registered: $taskPath$name @ $time (weekdays)"
}

if ($Remove) {
    foreach ($n in @("MorningReport", "PaperDaily", "TdxUpdate")) { Remove-TaskIfExists $n }
    Write-Host "all StockScanner tasks removed."
    exit 0
}

# 1) 晨报（run_daily.bat → reporter.scheduler.send_morning_report，含推送心跳自检）
$bat = Join-Path $root "run_daily.bat"
$log = Join-Path $logDir "morning_report.log"
Register-DailyTask "MorningReport" "08:45" "cmd.exe" "/c `"`"$bat`" >> `"$log`" 2>&1`""

# 2) paper trading 日报（scripts/paper_daily.bat）
$bat = Join-Path $root "scripts\paper_daily.bat"
$log = Join-Path $logDir "paper_daily.log"
Register-DailyTask "PaperDaily" "16:00" "cmd.exe" "/c `"`"$bat`" >> `"$log`" 2>&1`""

# 3) TDX 数据更新——直接调 python（update_tdx_data.bat 以 pause 结尾，不能无人值守）
$py = Join-Path $root "scripts\update_tdx_data.py"
$log = Join-Path $logDir "tdx_update.log"
Register-DailyTask "TdxUpdate" "18:30" $python "`"$py`" >> `"$log`" 2>&1"

Write-Host ""
Write-Host "验证：Get-ScheduledTask -TaskPath '\StockScanner\'"
Write-Host "心跳兜底：晨报每次运行会自检 push_log 末条成功推送是否超 2 个交易日（reporter/heartbeat.py）"
