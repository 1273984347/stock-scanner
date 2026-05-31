"""基准对比 —— 多指数基准收益计算"""

import logging
import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# A 股主要指数代码
BENCHMARK_INDICES = {
    "hs300": {"code": "000300", "name": "沪深300"},
    "zz500": {"code": "000905", "name": "中证500"},
    "zz1000": {"code": "000852", "name": "中证1000"},
}


@dataclass
class BenchmarkResult:
    """基准对比结果"""
    name: str
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float
    win_rate: float


def get_benchmark_returns(
    index_code: str,
    start_date: str = None,
    end_date: str = None,
    days: int = 250,
) -> pd.DataFrame:
    """获取基准指数收益序列

    Args:
        index_code: 指数代码
        start_date: 起始日期
        end_date: 结束日期
        days: 回溯天数

    Returns:
        DataFrame with date and daily_return columns
    """
    try:
        from lib.data_layer import get_index_hist
        df = get_index_hist(index_code, days=days)
        if df.empty:
            return pd.DataFrame()

        df = df.copy()
        if "close" in df.columns:
            df["daily_return"] = df["close"].pct_change()
        return df[["date", "daily_return"]].dropna()
    except Exception as e:
        logger.debug(f"获取基准 {index_code} 失败: {e}")
        return pd.DataFrame()


def compute_benchmark_metrics(
    returns: pd.DataFrame,
    name: str = "",
) -> Optional[BenchmarkResult]:
    """计算基准指标"""
    if returns.empty or "daily_return" not in returns.columns:
        return None

    daily = returns["daily_return"].values
    total_return = float((1 + daily).prod() - 1)
    n_days = len(daily)
    annual_return = float((1 + total_return) ** (252 / max(n_days, 1)) - 1)

    # 最大回撤
    cum = (1 + daily).cumprod()
    peak = cum.cummax()
    dd = (peak - cum) / peak
    max_drawdown = float(dd.max())

    # 夏普
    std = float(daily.std())
    sharpe = float(daily.mean() / std * (252 ** 0.5)) if std > 0 else 0

    # 胜率
    win_rate = float((daily > 0).mean())

    return BenchmarkResult(
        name=name,
        total_return=round(total_return, 4),
        annual_return=round(annual_return, 4),
        max_drawdown=round(max_drawdown, 4),
        sharpe=round(sharpe, 3),
        win_rate=round(win_rate, 3),
    )


def compare_with_benchmarks(
    strategy_return: float,
    strategy_sharpe: float,
    days: int = 250,
) -> Dict:
    """与多个基准对比

    Args:
        strategy_return: 策略总收益
        strategy_sharpe: 策略夏普
        days: 对比天数

    Returns:
        对比结果字典
    """
    benchmarks = {}

    for key, info in BENCHMARK_INDICES.items():
        returns = get_benchmark_returns(info["code"], days=days)
        metrics = compute_benchmark_metrics(returns, info["name"])
        if metrics:
            benchmarks[key] = {
                "name": info["name"],
                "total_return": metrics.total_return,
                "sharpe": metrics.sharpe,
                "max_drawdown": metrics.max_drawdown,
                "excess_return": round(strategy_return - metrics.total_return, 4),
                "excess_sharpe": round(strategy_sharpe - metrics.sharpe, 3),
            }

    return {
        "strategy": {"total_return": strategy_return, "sharpe": strategy_sharpe},
        "benchmarks": benchmarks,
    }


def format_benchmark_comparison(result: Dict) -> str:
    """格式化基准对比"""
    lines = ["## 基准对比", ""]

    strat = result.get("strategy", {})
    lines.append(f"策略: 收益 {strat.get('total_return', 0):.2%}, 夏普 {strat.get('sharpe', 0):.3f}")
    lines.append("")
    lines.append("| 基准 | 收益 | 夏普 | 最大回撤 | 超额收益 | 超额夏普 |")
    lines.append("|------|------|------|----------|----------|----------|")

    for key, bm in result.get("benchmarks", {}).items():
        lines.append(
            f"| {bm['name']} | {bm['total_return']:.2%} | {bm['sharpe']:.3f} | "
            f"{bm['max_drawdown']:.2%} | {bm['excess_return']:+.2%} | {bm['excess_sharpe']:+.3f} |"
        )

    return "\n".join(lines)
