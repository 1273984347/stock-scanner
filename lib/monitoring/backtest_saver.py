"""回测结果自动保存工具

所有回测脚本共用，统一格式存入 cache/backtest_results/。
"""
import json

# §4.2 P1 修复 (2026-06-08): TRADING_DAYS_PER_YEAR 常量
TRADING_DAYS_PER_YEAR = 252  # A 股年化交易天数 (硬编码, 简化)

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "cache" / "backtest_results"


def save_backtest_result(
    script_name: str,
    nav: np.ndarray,
    trades: Optional[List[Dict]] = None,
    args: Optional[Dict] = None,
    extra: Optional[Dict] = None,
) -> Path:
    """保存回测结果到 JSON 文件

    Args:
        script_name: 脚本标识（如 "engine", "stress", "rebalance"）
        nav: 净值序列（nav[0] 应为 1.0）
        trades: 交易记录列表（可选）
        args: 回测参数（可选）
        extra: 额外信息（可选）

    Returns:
        保存的文件路径
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"{script_name}_{ts}.json"

    # 计算核心指标
    metrics = _compute_metrics(nav, trades)

    result = {
        "timestamp": ts,
        "script": script_name,
        "args": args or {},
        "metrics": metrics,
        "nav": nav.tolist() if len(nav) > 0 else [],
    }
    if extra:
        result["extra"] = extra

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"回测结果已保存: {out_path}")
    return out_path


def _compute_metrics(nav: np.ndarray, trades: Optional[List[Dict]] = None) -> Dict:
    """从净值序列计算核心指标"""
    if len(nav) < 2:
        return {}

    daily_returns = np.diff(nav) / nav[:-1]
    n_years = len(nav) / TRADING_DAYS_PER_YEAR
    total_return = float(nav[-1] / nav[0] - 1)
    annualized = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

    peak = np.maximum.accumulate(nav)
    drawdown = (nav - peak) / peak
    max_drawdown = float(drawdown.min())

    std = float(daily_returns.std())
    sharpe = float(daily_returns.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR)) if std > 0 else 0

    metrics = {
        "total_return": round(total_return, 6),
        "annual_return": round(annualized, 6),
        "max_drawdown": round(max_drawdown, 6),
        "sharpe": round(sharpe, 4),
        "n_days": len(nav),
    }

    # 交易统计
    if trades:
        n_trades = len(trades)
        # P2-3: 显式拆分 win/loss（不再仅算 ratio, 暴露 losses 笔数与金额）
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) < 0]
        n_wins = len(wins)
        n_losses = len(losses)
        win_pnls = [t["pnl"] for t in wins]
        loss_pnls = [t["pnl"] for t in losses]  # 负数
        total_win_pnl = float(sum(win_pnls)) if win_pnls else 0.0
        total_loss_pnl = float(sum(loss_pnls)) if loss_pnls else 0.0
        avg_win = float(np.mean(win_pnls)) if win_pnls else 0.0
        avg_loss = float(np.mean(loss_pnls)) if loss_pnls else 0.0
        # win/loss ratio (avg_win / abs(avg_loss)) — 经典 Kelly/期望值公式
        win_loss_ratio = (avg_win / abs(avg_loss)) if (wins and losses and avg_loss != 0) else 0.0
        # 注: 0.0 在 caller 表示 '数据缺失' (非 '极小值'), 与 None 区分需 caller 文档化
        # profit factor (gross_profit / |gross_loss|) — 全赢时定义为 inf
        if total_loss_pnl < 0 and total_win_pnl > 0:
            profit_factor = total_win_pnl / abs(total_loss_pnl)
        elif total_win_pnl > 0 and total_loss_pnl == 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0
        # expectancy = E[pnl per trade]
        expectancy = (total_win_pnl + total_loss_pnl) / n_trades if n_trades > 0 else 0.0

        metrics["n_trades"] = n_trades
        metrics["n_wins"] = n_wins
        metrics["n_losses"] = n_losses
        metrics["win_rate"] = round(n_wins / n_trades, 4) if n_trades > 0 else 0
        metrics["avg_win"] = round(avg_win, 6)
        metrics["avg_loss"] = round(avg_loss, 6)
        metrics["total_win_pnl"] = round(total_win_pnl, 6)
        metrics["total_loss_pnl"] = round(total_loss_pnl, 6)
        metrics["win_loss_ratio"] = round(win_loss_ratio, 4)
        metrics["profit_factor"] = round(profit_factor, 4) if profit_factor != float("inf") else float("inf")
        metrics["expectancy"] = round(expectancy, 6)

        # 退出原因统计
        exit_counts = {}
        for t in trades:
            reason = t.get("reason", "unknown")
            exit_counts[reason] = exit_counts.get(reason, 0) + 1
        metrics["exit_counts"] = exit_counts

    return metrics
