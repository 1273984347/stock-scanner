"""回测结果自动保存工具

所有回测脚本共用，统一格式存入 cache/backtest_results/。
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "cache" / "backtest_results"


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
    n_years = len(nav) / 252
    total_return = float(nav[-1] / nav[0] - 1)
    annualized = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

    peak = np.maximum.accumulate(nav)
    drawdown = (nav - peak) / peak
    max_drawdown = float(drawdown.min())

    std = float(daily_returns.std())
    sharpe = float(daily_returns.mean() / std * np.sqrt(252)) if std > 0 else 0

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
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) < 0]
        metrics["n_trades"] = n_trades
        metrics["win_rate"] = round(len(wins) / n_trades, 4) if n_trades > 0 else 0
        metrics["avg_win"] = round(float(np.mean([t["pnl"] for t in wins])), 6) if wins else 0
        metrics["avg_loss"] = round(float(np.mean([t["pnl"] for t in losses])), 6) if losses else 0

        # 退出原因统计
        exit_counts = {}
        for t in trades:
            reason = t.get("reason", "unknown")
            exit_counts[reason] = exit_counts.get(reason, 0) + 1
        metrics["exit_counts"] = exit_counts

    return metrics
