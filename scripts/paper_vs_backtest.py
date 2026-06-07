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
from typing import Optional

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

EQUITY_FILE = Path("cache/paper_equity.json")
EQUITY_FILE.parent.mkdir(parents=True, exist_ok=True)

HS300_FILE = Path("cache/hs300_daily.json")
HS300_FILE.parent.mkdir(parents=True, exist_ok=True)


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

    # 4.5 追加 equity 数据点（让 rolling DD / cumulative / alpha 有数据）
    equity_value = _compute_current_equity()
    append_equity_point(equity_value)

    record_daily_snapshot(paper_metrics)
    print(f"\n  [完成] {datetime.now().isoformat()}")


def _compute_current_equity(initial_capital: float = 1_000_000.0) -> float:
    """计算当前 equity = 初始资金 + 已实现盈亏 + 浮动盈亏"""
    from paper_trading import _load_json, TRADES_FILE, POSITIONS_FILE

    trades = _load_json(TRADES_FILE, [])
    positions = _load_json(POSITIONS_FILE, {})

    # 已实现盈亏: 假设每笔仓位 10% 资金, 实际收益 = 初始资金 * 仓位% * pnl_pct
    # 简化: 累计 pnl_pct (复利) — 与 backtest engine 一致
    cum_return = 1.0
    for t in trades:
        cum_return *= (1 + t.get("pnl_pct", 0) / 100)

    # 浮动盈亏: 当前持仓
    unrealized = 0
    for code, pos in positions.items():
        unrealized += pos.get("pnl_pct", 0) / 100

    return initial_capital * cum_return * (1 + unrealized)


def extract_paper_metrics() -> dict:
    """从 paper_trading 存储的 JSON 文件提取 metrics（增强版）

    扩展：daily_pnl / cumulative_return / rolling_max_drawdown /
          signals_today / exit_reasons / hs300_daily / alpha_daily / days_running
    """
    from paper_trading import _load_json, TRADES_FILE, POSITIONS_FILE, SIGNALS_LOG

    trades = _load_json(TRADES_FILE, [])
    positions = _load_json(POSITIONS_FILE, {})

    # ---- 基础统计 ----
    df_trades = trades if isinstance(trades, list) else []
    total = len(df_trades)
    wins = [t for t in df_trades if t.get('pnl_pct', 0) > 0]
    losses = [t for t in df_trades if t.get('pnl_pct', 0) <= 0]

    win_rate = len(wins) / total * 100 if total > 0 else 0
    avg_pnl = sum(t.get('pnl_pct', 0) for t in df_trades) / total if total > 0 else 0
    avg_win = sum(t.get('pnl_pct', 0) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.get('pnl_pct', 0) for t in losses) / len(losses) if losses else 0
    pf = abs(sum(t.get('pnl_pct', 0) for t in wins) / sum(t.get('pnl_pct', 0) for t in losses)) if losses and sum(t.get('pnl_pct', 0) for t in losses) != 0 else 0

    # ---- 当日盈亏 + 累计 (从 equity curve 增量) ----
    equity = _load_equity_curve()
    if len(equity) >= 2:
        last_eq = equity[-1]["equity"]
        prev_eq = equity[-2]["equity"]
        daily_pnl_pct = round((last_eq / prev_eq - 1) * 100, 2)
        cumulative_return_pct = round((last_eq / equity[0]["equity"] - 1) * 100, 2)
    else:
        daily_pnl_pct = 0
        cumulative_return_pct = 0

    # ---- 滚动最大回撤 ----
    rolling_dd = compute_rolling_drawdown(equity)

    # ---- 今日信号数 ----
    today = datetime.now().strftime("%Y-%m-%d")
    signals_today = _count_signals_today(SIGNALS_LOG, today)

    # ---- 退出原因分布 ----
    exit_reasons = {}
    for t in df_trades:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    # ---- HS300 基准 + alpha ----
    hs300_today = _get_hs300_daily()
    alpha_daily = round(daily_pnl_pct - hs300_today, 2) if hs300_today is not None else 0

    return {
        # 基础
        "total_trades": total,
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(pf, 2),
        "open_positions": len(positions),
        # 扩展
        "daily_pnl": daily_pnl_pct,
        "cumulative_return": cumulative_return_pct,
        "rolling_max_drawdown": rolling_dd,
        "signals_today": signals_today,
        "exit_reasons": exit_reasons,
        "hs300_daily": round(hs300_today, 2) if hs300_today is not None else 0,
        "alpha_daily": alpha_daily,
        "days_running": len(equity),
        # 标记
        "_partial": total == 0,
    }


# ========== Equity Curve & HS300 (新增) ==========

def _load_equity_curve() -> list:
    """加载 equity curve (ST32 风格：try/except + default)"""
    if not EQUITY_FILE.exists():
        return []
    try:
        with open(EQUITY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_equity_curve(equity: list) -> None:
    """保存 equity curve (ST32: 原子写入)"""
    if not equity:
        return
    tmp = EQUITY_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(equity, f, ensure_ascii=False, indent=2)
    os.replace(tmp, EQUITY_FILE)


def append_equity_point(equity_value: float) -> None:
    """追加 equity 数据点（保留最近 252 个交易日 = 1 年）"""
    equity = _load_equity_curve()
    point = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "captured_at": datetime.now().isoformat(),
        "equity": round(equity_value, 4),
    }
    equity.append(point)
    equity = equity[-252:]
    _save_equity_curve(equity)


def compute_rolling_drawdown(equity: list) -> float:
    """计算当前 rolling 最大回撤 %"""
    if len(equity) < 2:
        return 0
    peak = equity[0]["equity"]
    max_dd = 0
    for p in equity:
        if p["equity"] > peak:
            peak = p["equity"]
        dd = (p["equity"] - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _count_signals_today(signals_log_path: Path, today: str) -> int:
    """统计今日 signals 数量 (ST28/ST30: 不假设 JSON 格式正确)"""
    if not signals_log_path.exists():
        return 0
    count = 0
    try:
        with open(signals_log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = json.loads(line)
                    if s.get("date") == today:
                        count += 1
                except json.JSONDecodeError:
                    continue
    except OSError:
        return 0
    return count


def _get_hs300_daily() -> Optional[float]:
    """获取 HS300 当日涨跌幅 % — TDX 本地优先，akshare 兜底

    ST21: 失败返回 None，不返回 0（0 = "中性"）
    """
    # 1. TDX 本地
    try:
        from pytdx.reader import TdxDailyBarReader
        reader = TdxDailyBarReader()
        fp = "D:/TDX/vipdoc/sh/lday/sh000300.day"
        if Path(fp).exists():
            df = reader.get_df(fp)
            if df is not None and len(df) >= 2:
                last_close = float(df.iloc[-1]["close"])
                prev_close = float(df.iloc[-2]["close"])
                return round((last_close / prev_close - 1) * 100, 2)
    except Exception:
        pass
    # 2. akshare 兜底
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is not None and len(df) >= 2:
            return round((float(df.iloc[-1]["close"]) / float(df.iloc[-2]["close"]) - 1) * 100, 2)
    except Exception:
        return None
    return None


def run_compare():
    """1-2 月后做对比：paper 实测 vs 回测基线"""
    snapshots = load_paper_snapshots()
    if not snapshots:
        print(f"[ERR] 无历史快照。请先跑 --mode daily 累积数据。")
        return

    first = snapshots[0]
    last = snapshots[-1]

    # 计算 paper 实测累计（back-compat: 老 snapshot 可能没有扩展字段）
    paper_first = first["paper_metrics"]
    paper_last = last["paper_metrics"]
    pg = lambda k, default=0: paper_last.get(k, default)  # 简化访问

    # 计算天数
    days = (datetime.fromisoformat(last["captured_at"]) - datetime.fromisoformat(first["captured_at"])).days

    print(f"\n{'='*60}")
    print(f"  Paper Trading vs 回测对比报告")
    print(f"{'='*60}")
    print(f"  数据窗口: {first['date']} 至 {last['date']} ({days} 天)")
    print(f"  快照数: {len(snapshots)}")

    # 对比指标（基础 + 扩展）
    base = BACKTEST_BASELINE["metrics"]
    print(f"\n  {'指标':<30} {'回测基线':>12} {'Paper 实测':>12} {'偏差':>12}")
    print(f"  {'-'*70}")

    comparisons = [
        # 基础（back-compat: 用 .get 保底）
        ("总交易数", base["total_trades"], pg("total_trades"), "absolute"),
        ("胜率 (%)", base["win_rate"], pg("win_rate"), "relative"),
        ("平均收益/笔 (%)", base["avg_pnl"], pg("avg_pnl"), "relative"),
        ("盈亏比", base["profit_factor"], pg("profit_factor"), "relative"),
        # 扩展
        ("累计收益 (%)", 0, pg("cumulative_return"), "relative"),
        ("滚动最大回撤 (%)", base["max_drawdown"], pg("rolling_max_drawdown"), "relative"),
        ("当日 alpha (%)", 0, pg("alpha_daily"), "relative"),
        ("今日信号数", 0, pg("signals_today"), "absolute"),
        ("当前持仓数", 0, pg("open_positions"), "absolute"),
        ("运行天数", 0, pg("days_running"), "absolute"),
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

    # 偏差评估（back-compat safe）
    print(f"\n  [审计] 偏差评估:")

    # 检测数据不足
    paper_total = pg("total_trades")
    if paper_total == 0 or pg("days_running", 0) < 5:
        print(f"    [INFO] Paper 数据不足 (trades={paper_total}, days={pg('days_running', 0)})")
        print(f"           建议至少累积 5 个交易日数据后再做对比")
        print(f"           当前 baseline 总交易 {base['total_trades']} 笔，paper 0 笔属正常")
    else:
        win_rate_diff = abs(pg("win_rate") - base["win_rate"])
        if win_rate_diff < 10:
            print(f"    [OK] 胜率偏差 {win_rate_diff:.1f}% (回测预期偏差 < 10%)")
        elif win_rate_diff < 20:
            print(f"    [WARN] 胜率偏差 {win_rate_diff:.1f}% (10-20% 需关注)")
        else:
            print(f"    [P0] 胜率偏差 {win_rate_diff:.1f}% (> 20% 远超回测)")

        pf_diff = abs(pg("profit_factor") - base["profit_factor"])
        if pf_diff < 0.5:
            print(f"    [OK] 盈亏比偏差 {pf_diff:.2f} (回测预期偏差 < 0.5)")
        else:
            print(f"    [WARN] 盈亏比偏差 {pf_diff:.2f} (> 0.5 需关注)")

        cum = pg("cumulative_return")
        if cum > 0:
            print(f"    [INFO] Paper 累计收益 {cum:.2f}% (无基线对比，需 60 天后看趋势)")

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
