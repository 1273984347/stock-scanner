# scripts/daily_store_sync.ps1 — 本地仓每日保鲜链（增量评审 Δ2，2026-11-22）
#
# 链路：交易日历预热 → 全域同步（kline/kline_raw/index/snapshot/events/fundflow）
#       → --content 内容判据。由 bootstrap_schedules.ps1 的 StoreDataSync 任务
#       每个交易日 19:00 调用；也可手动执行：
#   powershell -ExecutionPolicy Bypass -File scripts\daily_store_sync.ps1
#
# 设计：
# - 日历预热先行（C16 挂账收口）：新进程首刷可能瞬态失败（15s 超时 → 300s 冷却），
#   预热把失败隔离在本任务里，后续 data_sync 的交易日门退化行为有 warning 留痕；
# - --skip-non-trading：非交易日零网络零写仓（交易日历单源判定）；
# - 全链日志追加到 logs\scheduled\store_sync.log；失败 exit 1（任务计划可见），
#   推送心跳哨兵照旧兜底「任务有没有跑」。

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = (Get-Command python).Source
$log = Join-Path $root "logs\scheduled\store_sync.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Log([string]$msg) {
    Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
}

Log "=== store sync chain start ==="

# 1) 交易日历预热（preheat 自带失败留痕，不抛）
& $py -c "from lib.data.trade_calendar import preheat; h = preheat(); print('calendar:', h['max_date'], h['cached_days'])" >> $log 2>&1
if ($LASTEXITCODE -ne 0) { Log "warn: 日历预热失败（同步继续，交易日门退化周末判断）" }

# 2) 全域同步（kline+kline_raw 全市场 ~100min @4workers，ADR-0036 实测口径）
& $py (Join-Path $root "scripts\data_sync.py") `
    --datasets kline,kline_raw,index,snapshot,events,fundflow `
    --universe all --workers 4 --resume --skip-non-trading >> $log 2>&1
$syncExit = $LASTEXITCODE
Log "data_sync exit=$syncExit"

# 3) 内容判据（P-122/123/124，D-4 后含复权口径第 4 判据）——只读盘
& $py (Join-Path $root "scripts\data_sync.py") --content >> $log 2>&1
$contentExit = $LASTEXITCODE
Log "--content exit=$contentExit"
Log "=== store sync chain end ==="

if ($syncExit -ne 0 -or $contentExit -ne 0) { exit 1 }
exit 0
