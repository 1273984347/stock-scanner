"""
Paper Trading 启动 + 与回测对比脚本
=====================================

启动流程：
1. 加载修复后的回测基线（v3_hybrid, 750 天, HS300 代理 100 只）
2. 调用 paper_trading 现有函数做当日扫描+开仓+持仓更新
3. 每日记录 paper_trading metrics 到 logs/paper_tracking.jsonl
4. 1-2 月后用此脚本对比 paper_trading 实测 vs 回测基线

用法：
  python scripts/paper_vs_backtest.py --mode daily    # 跑一次日报+报告
  python scripts/paper_vs_backtest.py --mode compare # 跑 1-2 月后做对比

依赖：paper_trading.py 现有函数
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

# 修复后回测基线（v3_hybrid, 750 天, HS300 代理 100 只，2026-06-05 跑出）
BACKTEST_BASELINE = {
    "captured_at": "2026-06-05",
    "version": "v3_hybrid",
    "window_days": 750,
    "stock_pool_size": 100,
    "metrics": {
        "total_trades": 324,
        "win_rate": 43.2,
        "avg_pnl": 6.14,
        "avg_win": 25.52,
        "avg_loss": -9.61,
        "profit_factor": 2.20,
        "max_drawdown": -22.90,
        "total_return_pct": 198.81,
        "survivorship_bias_adjusted_return_pct": 192.85,  # 198.81 * 0.97
    },
    "audit_fixes_applied": ["P0-1 RiskManager 集成", "P0-2 跳空低开", "P1-1 印花税", "P1-2 连续亏损", "P1-3 偏差标注"],
}

TRACKING_LOG = Path("logs/paper_tracking.jsonl")
TRACKING_LOG.parent.mkdir(parents=True, exist_ok=True)


def record_daily_snapshot(paper_metrics: dict):
    """记录每日 paper trading 快照到 JSONL"""
    snapshot = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "captured_at": datetime.now().isoformat(),
        "paper_metrics": paper_metrics,
        "baseline": BACKTEST_BASELINE["metrics"],
    }
    with TRACKING_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    print(f"  [OK] 记录快照: {TRACKING_LOG}")


def load_paper_snapshots() -> list:
    """加载历史 paper trading 快照"""
    if not TRACKING_LOG.exists():
        return []
    snapshots = []
    with TRACKING_LOG.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                snapshots.append(json.loads(line))
    return snapshots


def run_daily():
    """跑一次日报：调用 paper_trading 扫描 + 报告"""
    from paper_trading import run_scan, update_positions, generate_report

    print(f"\n{'='*60}")
    print(f"  Paper Trading 日报 - {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'='*60}")

    # 1. 扫描
    print("\n[1/3] 扫描新机会...")
    signals = run_scan(preset="balanced")
    print(f"  发现 {len(signals) if signals else 0} 个信号")

    # 2. 更新持仓
    print("\n[2/3] 更新持仓...")
    update_positions()

    # 3. 报告
    print("\n[3/3] 生成报告...")
    generate_report()

    # 4. 提取 metrics 并记录
    paper_metrics = extract_paper_metrics()
    record_daily_snapshot(paper_metrics)
    print(f"\n  [完成] {datetime.now().isoformat()}")


def extract_paper_metrics() -> dict:
    """从 paper_trading 存储的 JSON 文件提取 metrics"""
    from paper_trading import _load_json, TRADES_FILE, POSITIONS_FILE

    trades = _load_json(TRADES_FILE, [])
    positions = _load_json(POSITIONS_FILE, {})

    if not trades:
        return {"total_trades": 0, "open_positions": len(positions)}

    df_trades = trades if isinstance(trades, list) else []
    total = len(df_trades)
    wins = [t for t in df_trades if t.get('pnl_pct', 0) > 0]
    losses = [t for t in df_trades if t.get('pnl_pct', 0) <= 0]

    win_rate = len(wins) / total * 100 if total > 0 else 0
    avg_pnl = sum(t.get('pnl_pct', 0) for t in df_trades) / total if total > 0 else 0
    avg_win = sum(t.get('pnl_pct', 0) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.get('pnl_pct', 0) for t in losses) / len(losses) if losses else 0
    pf = abs(sum(t.get('pnl_pct', 0) for t in wins) / sum(t.get('pnl_pct', 0) for t in losses)) if losses and sum(t.get('pnl_pct', 0) for t in losses) != 0 else 0

    return {
        "total_trades": total,
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(pf, 2),
        "open_positions": len(positions),
    }


def run_compare():
    """1-2 月后做对比：paper 实测 vs 回测基线"""
    snapshots = load_paper_snapshots()
    if not snapshots:
        print(f"[ERR] 无历史快照。请先跑 --mode daily 累积数据。")
        return

    first = snapshots[0]
    last = snapshots[-1]

    # 计算 paper 实测累计
    paper_first = first["paper_metrics"]
    paper_last = last["paper_metrics"]

    # 计算天数
    days = (datetime.fromisoformat(last["captured_at"]) - datetime.fromisoformat(first["captured_at"])).days

    print(f"\n{'='*60}")
    print(f"  Paper Trading vs 回测对比报告")
    print(f"{'='*60}")
    print(f"  数据窗口: {first['date']} 至 {last['date']} ({days} 天)")
    print(f"  快照数: {len(snapshots)}")

    # 对比指标
    base = BACKTEST_BASELINE["metrics"]
    print(f"\n  {'指标':<30} {'回测基线':>12} {'Paper 实测':>12} {'偏差':>12}")
    print(f"  {'-'*70}")

    comparisons = [
        ("总交易数", base["total_trades"], paper_last["total_trades"], "absolute"),
        ("胜率 (%)", base["win_rate"], paper_last["win_rate"], "relative"),
        ("平均收益/笔 (%)", base["avg_pnl"], paper_last["avg_pnl"], "relative"),
        ("盈亏比", base["profit_factor"], paper_last["profit_factor"], "relative"),
    ]
    for name, b, p, mode in comparisons:
        if mode == "relative":
            diff = p - b
            sign = "+" if diff > 0 else ""
            print(f"  {name:<30} {b:>12.2f} {p:>12.2f} {sign}{diff:>10.2f}")
        else:
            diff = p - b
            sign = "+" if diff > 0 else ""
            print(f"  {name:<30} {b:>12} {p:>12} {sign}{diff:>10}")

    # 偏差评估
    print(f"\n  [审计] 偏差评估:")
    win_rate_diff = abs(paper_last["win_rate"] - base["win_rate"])
    if win_rate_diff < 10:
        print(f"    [OK] 胜率偏差 {win_rate_diff:.1f}% (回测预期偏差 < 10%)")
    elif win_rate_diff < 20:
        print(f"    [WARN] 胜率偏差 {win_rate_diff:.1f}% (10-20% 需关注)")
    else:
        print(f"    [P0] 胜率偏差 {win_rate_diff:.1f}% (> 20% 远超回测)")

    pf_diff = abs(paper_last["profit_factor"] - base["profit_factor"])
    if pf_diff < 0.5:
        print(f"    [OK] 盈亏比偏差 {pf_diff:.2f} (回测预期偏差 < 0.5)")
    else:
        print(f"    [WARN] 盈亏比偏差 {pf_diff:.2f} (> 0.5 需关注)")

    print(f"\n  基线版本: {BACKTEST_BASELINE['version']}, window={BACKTEST_BASELINE['window_days']}天")
    print(f"  已应用修复: {', '.join(BACKTEST_BASELINE['audit_fixes_applied'])}")
    print(f"  快照日志: {TRACKING_LOG}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Paper Trading 启动 + 对比脚本")
    parser.add_argument("--mode", choices=["daily", "compare"], required=True,
                        help="daily=跑一次日报+记录; compare=1-2月后做对比")
    args = parser.parse_args()

    if args.mode == "daily":
        run_daily()
    elif args.mode == "compare":
        run_compare()


if __name__ == "__main__":
    main()
