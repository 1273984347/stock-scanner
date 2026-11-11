"""
绩效分析器 —— 回测结果分析、风险指标计算、归因分析
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional
from dataclasses import dataclass
import logging

from config import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """绩效指标"""
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    daily_var_95: float = 0.0
    daily_var_99: float = 0.0
    information_ratio: float = 0.0
    tracking_error: float = 0.0
    excess_return: float = 0.0
    turnover_rate: float = 0.0


class PerformanceAnalyzer:
    """绩效分析器"""

    def __init__(self, perf_df: pd.DataFrame, benchmark_df: pd.DataFrame = None,
                 risk_free_rate: float = 0.03):
        self.perf_df = perf_df.copy()
        self.benchmark_df = benchmark_df.copy() if benchmark_df is not None else pd.DataFrame()
        self.rf = risk_free_rate
        self.trading_days = TRADING_DAYS_PER_YEAR

    def analyze(self) -> PerformanceMetrics:
        """全面绩效分析"""
        m = PerformanceMetrics()
        df = self.perf_df

        if df.empty or "total_value" not in df.columns:
            return m

        returns = df["daily_return"].dropna()
        if len(returns) < 2:
            return m

        # 收益指标
        m.total_return = df["total_value"].iloc[-1] / df["total_value"].iloc[0] - 1
        years = max(len(returns) / self.trading_days, 1e-10)
        m.annual_return = (1 + m.total_return) ** (1 / years) - 1 if years > 0 else 0
        m.annual_volatility = returns.std() * np.sqrt(self.trading_days)

        # 风险调整收益
        excess = returns - self.rf / self.trading_days
        m.sharpe_ratio = (excess.mean() / returns.std() * np.sqrt(self.trading_days)
                          if returns.std() > 0 else 0)

        downside = returns[returns < 0]
        m.sortino_ratio = (excess.mean() / downside.std() * np.sqrt(self.trading_days)
                           if len(downside) > 0 and downside.std() > 0 else 0)

        # 最大回撤
        cumulative = df["total_value"] / df["total_value"].iloc[0]
        rolling_max = cumulative.cummax()
        drawdown = (cumulative - rolling_max) / rolling_max
        m.max_drawdown = drawdown.min()

        m.calmar_ratio = (m.annual_return / abs(m.max_drawdown)
                          if m.max_drawdown != 0 else 0)

        # 回撤持续期
        dd_start = None
        max_duration = 0
        for i, dd in enumerate(drawdown):
            if dd < 0 and dd_start is None:
                dd_start = i
            elif dd >= 0 and dd_start is not None:
                duration = i - dd_start
                max_duration = max(max_duration, duration)
                dd_start = None
        m.max_drawdown_duration = max_duration

        # 胜率 & 盈亏比
        positive = returns[returns > 0]
        negative = returns[returns < 0]
        m.win_rate = len(positive) / len(returns) if len(returns) > 0 else 0
        avg_win = positive.mean() if len(positive) > 0 else 0
        avg_loss = abs(negative.mean()) if len(negative) > 0 else 1
        m.profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # VaR
        m.daily_var_95 = returns.quantile(0.05)
        m.daily_var_99 = returns.quantile(0.01)

        # 相对基准
        if not self.benchmark_df.empty:
            bm_returns = self.benchmark_df.set_index("trade_date")
            aligned = pd.DataFrame({
                "strategy": returns,
                "benchmark": bm_returns["pct_chg"] / 100 if "pct_chg" in bm_returns.columns
                else bm_returns["close"].pct_change()
            }).dropna()

            if not aligned.empty:
                m.excess_return = m.total_return - (
                    (1 + aligned["benchmark"]).prod() - 1
                )
                tracking_diff = aligned["strategy"] - aligned["benchmark"]
                m.tracking_error = tracking_diff.std() * np.sqrt(self.trading_days)
                m.information_ratio = (
                    tracking_diff.mean() / tracking_diff.std() * np.sqrt(self.trading_days)
                    if tracking_diff.std() > 0 else 0
                )

        return m

    def get_monthly_returns(self) -> pd.DataFrame:
        """月度收益表"""
        df = self.perf_df.copy()
        if "trade_date" not in df.columns:
            return pd.DataFrame()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["year_month"] = df["trade_date"].dt.to_period("M")
        monthly = df.groupby("year_month")["daily_return"].apply(
            lambda x: (1 + x).prod() - 1
        )
        return monthly

    def get_yearly_returns(self) -> pd.DataFrame:
        """年度收益表"""
        df = self.perf_df.copy()
        if "trade_date" not in df.columns:
            return pd.DataFrame()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["year"] = df["trade_date"].dt.year
        yearly = df.groupby("year")["daily_return"].apply(
            lambda x: (1 + x).prod() - 1
        )
        return yearly

    def get_rolling_sharpe(self, window: int = 252) -> pd.Series:
        """滚动夏普比率"""
        returns = self.perf_df["daily_return"].dropna()
        rolling = returns.rolling(window).apply(
            lambda x: (x.mean() / x.std() * np.sqrt(self.trading_days))
            if x.std() > 0 else 0
        )
        return rolling

    def summary_report(self) -> str:
        """生成摘要报告"""
        m = self.analyze()
        lines = [
            "=" * 60,
            "                    回测绩效报告",
            "=" * 60,
            "",
            "【收益指标】",
            f"  累计收益率:     {m.total_return:>10.2%}",
            f"  年化收益率:     {m.annual_return:>10.2%}",
            f"  年化波动率:     {m.annual_volatility:>10.2%}",
            "",
            "【风险调整收益】",
            f"  夏普比率:       {m.sharpe_ratio:>10.2f}",
            f"  索提诺比率:     {m.sortino_ratio:>10.2f}",
            f"  卡玛比率:       {m.calmar_ratio:>10.2f}",
            "",
            "【风险指标】",
            f"  最大回撤:       {m.max_drawdown:>10.2%}",
            f"  最大回撤持续:   {m.max_drawdown_duration:>10} 天",
            f"  日 VaR (95%):   {m.daily_var_95:>10.2%}",
            f"  日 VaR (99%):   {m.daily_var_99:>10.2%}",
            "",
            "【交易统计】",
            f"  胜率:           {m.win_rate:>10.2%}",
            f"  盈亏比:         {m.profit_loss_ratio:>10.2f}",
            "",
        ]
        if m.information_ratio != 0:
            lines += [
                "【相对基准】",
                f"  超额收益:       {m.excess_return:>10.2%}",
                f"  信息比率:       {m.information_ratio:>10.2f}",
                f"  跟踪误差:       {m.tracking_error:>10.2%}",
                "",
            ]
        lines.append("=" * 60)
        return "\n".join(lines)