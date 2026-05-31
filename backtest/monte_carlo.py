"""蒙特卡洛模拟 -- 评估策略鲁棒性

通过随机打乱交易顺序，生成收益分布，
评估策略表现是否依赖于特定交易顺序。
"""

import logging
import numpy as np
from typing import List, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MonteCarloResult:
    """蒙特卡洛模拟结果"""
    n_simulations: int
    original_sharpe: float
    median_sharpe: float
    sharpe_5th: float
    sharpe_95th: float
    original_total_return: float
    median_total_return: float
    return_5th: float
    return_95th: float
    max_drawdown_median: float
    max_drawdown_95th: float
    prob_positive_return: float
    prob_sharpe_gt_1: float


def run_monte_carlo(
    trades: List[Dict],
    n_simulations: int = 1000,
    initial_capital: float = 1_000_000,
) -> MonteCarloResult:
    """运行蒙特卡洛模拟

    Args:
        trades: 交易记录列表（需有 pnl_pct 字段）
        n_simulations: 模拟次数
        initial_capital: 初始资金

    Returns:
        MonteCarloResult
    """
    if not trades or len(trades) < 5:
        return MonteCarloResult(
            n_simulations=0, original_sharpe=0, median_sharpe=0,
            sharpe_5th=0, sharpe_95th=0, original_total_return=0,
            median_total_return=0, return_5th=0, return_95th=0,
            max_drawdown_median=0, max_drawdown_95th=0,
            prob_positive_return=0, prob_sharpe_gt_1=0,
        )

    pnls = np.array([t.get("pnl_pct", 0) for t in trades])

    # 原始指标
    original_sharpe = _calc_sharpe(pnls)
    original_return = float(np.sum(pnls))

    # 模拟
    sharpes = []
    returns = []
    drawdowns = []

    for _ in range(n_simulations):
        shuffled = np.random.permutation(pnls)

        # 计算累计收益曲线
        equity = initial_capital
        peak = equity
        max_dd = 0
        equity_curve = [equity]

        for pnl in shuffled:
            equity *= (1 + pnl)
            equity_curve.append(equity)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        sharpes.append(_calc_sharpe(shuffled))
        returns.append(float((equity - initial_capital) / initial_capital))
        drawdowns.append(max_dd)

    sharpes = np.array(sharpes)
    returns = np.array(returns)
    drawdowns = np.array(drawdowns)

    return MonteCarloResult(
        n_simulations=n_simulations,
        original_sharpe=round(original_sharpe, 3),
        median_sharpe=round(float(np.median(sharpes)), 3),
        sharpe_5th=round(float(np.percentile(sharpes, 5)), 3),
        sharpe_95th=round(float(np.percentile(sharpes, 95)), 3),
        original_total_return=round(original_return, 4),
        median_total_return=round(float(np.median(returns)), 4),
        return_5th=round(float(np.percentile(returns, 5)), 4),
        return_95th=round(float(np.percentile(returns, 95)), 4),
        max_drawdown_median=round(float(np.median(drawdowns)), 4),
        max_drawdown_95th=round(float(np.percentile(drawdowns, 95)), 4),
        prob_positive_return=round(float(np.mean(returns > 0)), 3),
        prob_sharpe_gt_1=round(float(np.mean(sharpes > 1.0)), 3),
    )


def _calc_sharpe(pnls: np.ndarray, risk_free: float = 0) -> float:
    """计算年化夏普比率"""
    if len(pnls) < 2:
        return 0
    excess = pnls - risk_free
    std = float(np.std(excess))
    if std == 0:
        return 0
    return float(np.mean(excess) / std * np.sqrt(252))


def format_monte_carlo(result: MonteCarloResult) -> str:
    """格式化蒙特卡洛结果"""
    return f"""## 蒙特卡洛模拟结果（{result.n_simulations} 次）

| 指标 | 原始 | 中位数 | 5%分位 | 95%分位 |
|------|------|--------|--------|---------|
| 夏普比率 | {result.original_sharpe} | {result.median_sharpe} | {result.sharpe_5th} | {result.sharpe_95th} |
| 总收益 | {result.original_total_return:.2%} | {result.median_total_return:.2%} | {result.return_5th:.2%} | {result.return_95th:.2%} |
| 最大回撤 | - | {result.max_drawdown_median:.2%} | - | {result.max_drawdown_95th:.2%} |

- 正收益概率: **{result.prob_positive_return:.1%}**
- 夏普>1概率: **{result.prob_sharpe_gt_1:.1%}**
"""
