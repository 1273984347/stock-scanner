# scripts/backup_store.ps1 — data/store 每日快照（增量评审 Δ1，2026-11-22）
#
# 背景：备份端点白名单明示排除 data/store（routers/backup.py:40-41「可再生不进 zip」），
# 但「可再生」依赖 3 条无契约网页网关——准生产单机下仓损毁 = RTO 以小时计且可能
# 部分失败。本脚本给 197MB 的仓一个滚动副本（RPO ≤ 1 天，恢复 = 拷回，分钟级）。
#
# 用法（由 bootstrap_schedules.ps1 的 StoreBackup 任务每日调用，也可手动）：
#   powershell -ExecutionPolicy Bypass -File scripts\backup_store.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\backup_store.ps1 -Dest D:\store_backup
#
# 策略（最小可行）：
# - robocopy /E 增量复制到**滚动目录**（默认 backups\store_current）；
# - 不用 /PURGE：源里误删/损坏删除不传播到备份（宁多勿少）；
# - 复制后用 manifest 行数做一次轻校验并写日志（对账真源仍是 data_sync --verify）。
# 局限（登记在评审文档 Δ1）：单机同盘副本防「仓坏了」不防「盘坏了」——异盘/异地
# 副本由 -Dest 指向另一物理盘实现，是否配置由用户按待补信息 #1 决定。

param(
    [string]$Dest = "",
    [int]$RobocopyRetries = 2
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$src = Join-Path $root "data\store"
if (-not $Dest) { $Dest = Join-Path $root "backups\store_current" }

if (-not (Test-Path $src)) {
    Write-Host "[backup-store] FAIL: 源目录不存在 $src"
    exit 1
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$t = Get-Date
# robocopy 退出码 0-7 = 成功（1=有复制），>=8 = 失败
$rc = Start-Process robocopy -ArgumentList "`"$src`"", "`"$Dest`"", "/E", "/NFL", "/NDL", "/NJH", "/R:$RobocopyRetries", "/W:5" -Wait -PassThru -NoNewWindow
if ($rc.ExitCode -ge 8) {
    Write-Host "[backup-store] FAIL: robocopy exit=$($rc.ExitCode)"
    exit 1
}

# 轻校验：备份侧文件数应 >= 源侧 manifest 记账的 files 之和（/E 不清理 ⇒ 只多不少）
$manifest = Join-Path $src "manifest.json"
$expected = 0
if (Test-Path $manifest) {
    try {
        $j = Get-Content $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $j.datasets.PSObject.Properties) { $expected += [int]$p.Value.files }
    } catch { Write-Host "[backup-store] warn: manifest 不可解析，跳过计数校验" }
}
$actual = (Get-ChildItem $Dest -Recurse -File | Where-Object { $_.Extension -ne ".lock" }).Count
$elapsed = [math]::Round(((Get-Date) - $t).TotalSeconds, 1)
Write-Host "[backup-store] OK files(source-manifest)=$expected files(backup)=$actual ${elapsed}s -> $Dest"
if ($expected -gt 0 -and $actual -lt $expected) {
    Write-Host "[backup-store] FAIL: 备份文件数少于 manifest 记账（复制不完整？）"
    exit 1
}
exit 0
