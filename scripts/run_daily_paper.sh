#!/usr/bin/env bash
# run_daily_paper.sh — 每日 paper trading 启动脚本
#
# 用途：
# 1. 每日盘后跑 paper trading 扫描+持仓更新+报告
# 2. 累计 snapshots 到 logs/paper_tracking.jsonl
# 3. 1-2 月后用 paper_vs_backtest.py --mode compare 对比
#
# 用法：
#   bash scripts/run_daily_paper.sh              # 跑一次
#   bash scripts/run_daily_paper.sh --dry-run    # 只打印命令不执行
#   bash scripts/run_daily_paper.sh --quiet      # 静默模式
#
# 集成到系统 crontab（建议盘后 16:00 跑）：
#   0 16 * * 1-5 cd /path/to/stock-scanner && bash scripts/run_daily_paper.sh >> logs/cron_paper.log 2>&1
#
# 退出码：
#   0 = 成功
#   1 = paper trading 失败
#   2 = 数据获取失败（网络/akshare 限流）

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT"

# 配置
PYTHON="${PYTHON:-python}"
LOG_DIR="logs"
TRACKING_LOG="$LOG_DIR/paper_tracking.jsonl"
DAILY_LOG="$LOG_DIR/paper_daily_$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"

# 参数解析
DRY_RUN=0
QUIET=0
for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=1 ;;
        --quiet)   QUIET=1 ;;
        *) echo "[ERROR] 未知参数: $arg"; exit 1 ;;
    esac
done

# 输出控制
if [ "$QUIET" -eq 1 ]; then
    exec 1>/dev/null
fi

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DAILY_LOG"
}

log "=========================================="
log "Paper Trading 启动 - $(date)"
log "=========================================="

# 1. 健康检查
log "[1/4] 健康检查..."
if ! $PYTHON -c "import sys; sys.path.insert(0, '.'); from paper_trading import run_scan" 2>/dev/null; then
    log "[ERROR] paper_trading 模块无法导入（依赖缺失？）"
    exit 1
fi

# 2. 跑 paper trading 日报
log "[2/4] 跑 paper trading 日报..."
if [ "$DRY_RUN" -eq 1 ]; then
    log "[DRY-RUN] 跳过执行: $PYTHON scripts/paper_vs_backtest.py --mode daily"
else
    if ! $PYTHON scripts/paper_vs_backtest.py --mode daily >> "$DAILY_LOG" 2>&1; then
        log "[ERROR] paper trading 日报失败，查看 $DAILY_LOG"
        exit 1
    fi
fi

# 3. 验证 tracking log
log "[3/4] 验证 tracking log..."
if [ ! -f "$TRACKING_LOG" ]; then
    log "[WARN] tracking log 不存在: $TRACKING_LOG（首次运行？）"
else
    SNAPSHOT_COUNT=$(wc -l < "$TRACKING_LOG")
    log "  已累计 snapshots: $SNAPSHOT_COUNT 行"
    LATEST_DATE=$(tail -1 "$TRACKING_LOG" | python -c "import sys, json; print(json.loads(sys.stdin.read())['date'])" 2>/dev/null || echo "未知")
    log "  最新 snapshot 日期: $LATEST_DATE"
fi

# 4. 报告
log "[4/4] 报告生成..."
log "  详细报告: $DAILY_LOG"
log "  Tracking log: $TRACKING_LOG"

log "=========================================="
log "Paper Trading 完成 - $(date)"
log "=========================================="

# 提示：1-2 月后跑对比
DAYS_RUN=$(find "$LOG_DIR" -name "paper_daily_*.log" 2>/dev/null | wc -l)
if [ "$DAYS_RUN" -ge 20 ]; then
    log ""
    log "已运行 $DAYS_RUN 天，建议运行对比："
    log "  $PYTHON scripts/paper_vs_backtest.py --mode compare"
fi
