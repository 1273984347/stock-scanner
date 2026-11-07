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


@dataclass
class BootstrapResult:
    """Bootstrap 置信区间结果"""
    n_bootstrap: int
    # 收益置信区间
    return_mean: float
    return_ci_lower: float
    return_ci_upper: float
    # 夏普置信区间
    sharpe_mean: float
    sharpe_ci_lower: float
    sharpe_ci_upper: float
    # 胜率置信区间
    win_rate_mean: float
    win_rate_ci_lower: float
    win_rate_ci_upper: float


def run_bootstrap(
    trades: List[Dict],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
) -> BootstrapResult:
    """Bootstrap 置信区间估计

    Args:
        trades: 交易记录列表
        n_bootstrap: 重采样次数
        confidence: 置信水平（默认 95%）

    Returns:
        BootstrapResult
    """
    if not trades or len(trades) < 5:
        return BootstrapResult(
            n_bootstrap=0, return_mean=0, return_ci_lower=0, return_ci_upper=0,
            sharpe_mean=0, sharpe_ci_lower=0, sharpe_ci_upper=0,
            win_rate_mean=0, win_rate_ci_lower=0, win_rate_ci_upper=0,
        )

    pnls = np.array([t.get("pnl_pct", 0) for t in trades])
    n = len(pnls)

    returns = []
    sharpes = []
    win_rates = []

    for _ in range(n_bootstrap):
        sample = np.random.choice(pnls, size=n, replace=True)
        returns.append(float(np.sum(sample)))
        sharpes.append(_calc_sharpe(sample))
        win_rates.append(float(np.mean(sample > 0)))

    returns = np.array(returns)
    sharpes = np.array(sharpes)
    win_rates = np.array(win_rates)

    alpha = (1 - confidence) / 2
    ci_lo = alpha * 100
    ci_hi = (1 - alpha) * 100

    return BootstrapResult(
        n_bootstrap=n_bootstrap,
        return_mean=round(float(np.mean(returns)), 4),
        return_ci_lower=round(float(np.percentile(returns, ci_lo)), 4),
        return_ci_upper=round(float(np.percentile(returns, ci_hi)), 4),
        sharpe_mean=round(float(np.mean(sharpes)), 3),
        sharpe_ci_lower=round(float(np.percentile(sharpes, ci_lo)), 3),
        sharpe_ci_upper=round(float(np.percentile(sharpes, ci_hi)), 3),
        win_rate_mean=round(float(np.mean(win_rates)), 3),
        win_rate_ci_lower=round(float(np.percentile(win_rates, ci_lo)), 3),
        win_rate_ci_upper=round(float(np.percentile(win_rates, ci_hi)), 3),
    )


def format_bootstrap(result: BootstrapResult) -> str:
    """格式化 Bootstrap 结果"""
    return f"""## Bootstrap 置信区间（{result.n_bootstrap} 次重采样，95% CI）

| 指标 | 均值 | 下界 | 上界 |
|------|------|------|------|
| 总收益 | {result.return_mean:.2%} | {result.return_ci_lower:.2%} | {result.return_ci_upper:.2%} |
| 夏普比率 | {result.sharpe_mean:.3f} | {result.sharpe_ci_lower:.3f} | {result.sharpe_ci_upper:.3f} |
| 胜率 | {result.win_rate_mean:.1%} | {result.win_rate_ci_lower:.1%} | {result.win_rate_ci_upper:.1%} |
"""


@dataclass
class CostSensitivityResult:
    """成本敏感性分析结果"""
    scenarios: List[Dict]
    # 每个场景: {"label": str, "commission": float, "slippage": float, "total_return": float, "sharpe": float}


def run_cost_sensitivity(
    trades: List[Dict],
    base_commission: float = 0.00025,
    base_slippage: float = 0.002,
) -> CostSensitivityResult:
    """交易成本敏感性分析

    Args:
        trades: 交易记录列表
        base_commission: 基础佣金率
        base_slippage: 基础滑点率

    Returns:
        CostSensitivityResult
    """
    if not trades:
        return CostSensitivityResult(scenarios=[])

    # 定义成本场景
    scenarios_def = [
        ("零成本", 0, 0),
        ("低成本", base_commission * 0.5, base_slippage * 0.5),
        ("基准", base_commission, base_slippage),
        ("高成本", base_commission * 2, base_slippage * 2),
        ("极高成本", base_commission * 5, base_slippage * 5),
    ]

    scenarios = []
    for label, comm, slip in scenarios_def:
        # 调整每笔交易的收益
        adjusted_pnls = []
        for t in trades:
            pnl = t.get("pnl_pct", 0)
            # 每笔交易扣除佣金和滑点（买卖各一次）
            cost = comm * 2 + slip * 2
            adjusted_pnls.append(pnl - cost)

        total_return = float(np.sum(adjusted_pnls))
        sharpe = _calc_sharpe(np.array(adjusted_pnls))

        scenarios.append({
            "label": label,
            "commission": comm,
            "slippage": slip,
            "cost_per_trade": round((comm * 2 + slip * 2) * 100, 3),
            "total_return": round(total_return, 4),
            "sharpe": round(sharpe, 3),
        })

    return CostSensitivityResult(scenarios=scenarios)


def format_cost_sensitivity(result: CostSensitivityResult) -> str:
    """格式化成本敏感性结果"""
    lines = [
        "## 交易成本敏感性分析",
        "",
        "| 场景 | 佣金 | 滑点 | 单笔成本 | 总收益 | 夏普 |",
        "|------|------|------|----------|--------|------|",
    ]

    for s in result.scenarios:
        lines.append(
            f"| {s['label']} | {s['commission']:.4%} | {s['slippage']:.3%} | "
            f"{s['cost_per_trade']:.3f}% | {s['total_return']:.2%} | {s['sharpe']:.3f} |"
        )

    return "\n".join(lines)
