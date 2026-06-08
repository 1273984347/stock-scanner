"""A/B 测试框架 — 两组策略参数并行回测对比

功能：
- 定义实验：两组策略参数（A 组 vs B 组）
- 并行回测：相同时间段、相同股票池
- 统计检验：t 检验比较 Sharpe/收益差异显著性
- 结果报告：胜率、p 值、置信区间
"""

import numpy as np

# §4.2 P1 修复 (2026-06-08): TRADING_DAYS_PER_YEAR 常量
TRADING_DAYS_PER_YEAR = 252  # A 股年化交易天数 (硬编码, 简化)

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from scipy import stats
import logging

logger = logging.getLogger(__name__)


@dataclass
class ABTestConfig:
    """A/B 测试配置"""
    symbols: List[str]
    days: int = 250
    group_a: Dict = field(default_factory=dict)  # A 组参数
    group_b: Dict = field(default_factory=dict)  # B 组参数
    num_runs: int = 10  # 重复次数（用于计算置信区间）


@dataclass
class ABTestResult:
    """A/B 测试结果"""
    group_a_name: str
    group_b_name: str
    # A 组指标
    a_sharpe: float
    a_return: float
    a_max_drawdown: float
    a_win_rate: float
    a_trades: int
    # B 组指标
    b_sharpe: float
    b_return: float
    b_max_drawdown: float
    b_win_rate: float
    b_trades: int
    # 统计检验
    sharpe_p_value: float
    return_p_value: float
    sharpe_diff: float
    return_diff: float
    # 置信区间
    sharpe_ci_lower: float
    sharpe_ci_upper: float
    return_ci_lower: float
    return_ci_upper: float
    # 结论
    winner: str
    confidence: str
    recommendation: str


class ABTester:
    """A/B 测试器"""

    def __init__(self):
        self._backtest_engine = None

    def _get_backtest_engine(self):
        """延迟加载回测引擎"""
        if self._backtest_engine is None:
            try:
                from backtest.engine import BacktestEngine
                self._backtest_engine = BacktestEngine()
            except ImportError:
                logger.warning("BacktestEngine not available, using mock")
                self._backtest_engine = self._create_mock_engine()
        return self._backtest_engine

    def _create_mock_engine(self):
        """创建模拟回测引擎（用于测试）"""
        class MockEngine:
            def run(self, symbols, days, params=None):
                # 模拟回测结果
                np.random.seed(hash(str(params)) % 2**32)
                returns = np.random.normal(0.0005, 0.02, days)
                cum_returns = (1 + returns).cumprod()
                total_return = cum_returns[-1] - 1
                sharpe = np.mean(returns) / np.std(returns) * np.sqrt(TRADING_DAYS_PER_YEAR)
                max_dd = np.min(cum_returns / np.maximum.accumulate(cum_returns) - 1)
                win_rate = np.mean(returns > 0)
                trades = np.random.randint(50, 200)

                return {
                    "total_return": round(total_return, 4),
                    "sharpe_ratio": round(sharpe, 3),
                    "max_drawdown": round(max_dd, 4),
                    "win_rate": round(win_rate, 4),
                    "trades": trades,
                    "returns": returns.tolist()
                }
        return MockEngine()

    def run_test(self, config: ABTestConfig) -> ABTestResult:
        """运行 A/B 测试 — bootstrap 配对检验

        P0-3 修复（2026-06-05）：旧版 num_runs=10 把同一段历史回测 10 次当 i.i.d. 样本做
        t 检验。但 A 和 B 用的是同一段历史，样本强相关，p 值是伪显著。

        新流程：
        1. A 和 B 各跑 1 次，获取日收益序列
        2. 用 bootstrap（有放回重采样日收益）生成 N 个 (Sharpe_B - Sharpe_A, Return_B - Return_A)
        3. 配对 t 检验（diff 序列本身是 i.i.d.）
        4. 百分位 CI（不依赖正态假设）
        """
        engine = self._get_backtest_engine()
        n_bootstrap = max(config.num_runs, 1000)  # 至少 1000 次 bootstrap

        # 1. A 和 B 各跑 1 次
        a_result = engine.run(config.symbols, config.days, config.group_a)
        b_result = engine.run(config.symbols, config.days, config.group_b)

        a_daily = np.asarray(a_result.get("returns", []), dtype=float)
        b_daily = np.asarray(b_result.get("returns", []), dtype=float)

        # 若引擎未返回日收益序列 → 降级为点估计 + 配对样本 t 检验
        if len(a_daily) == 0 or len(b_daily) == 0:
            logger.warning("引擎未返回日收益序列，bootstrap 不可用，降级为配对点估计")
            return self._run_test_degraded(config, a_result, b_result)

        n = min(len(a_daily), len(b_daily))
        a_daily = a_daily[:n]
        b_daily = b_daily[:n]

        # 2. Bootstrap
        rng = np.random.default_rng(42)  # 可复现
        sharpe_diffs = np.empty(n_bootstrap)
        return_diffs = np.empty(n_bootstrap)
        a_sharpes = np.empty(n_bootstrap)
        b_sharpes = np.empty(n_bootstrap)
        a_total_returns = np.empty(n_bootstrap)
        b_total_returns = np.empty(n_bootstrap)

        for i in range(n_bootstrap):
            idx = rng.integers(0, n, size=n)
            a_boot = a_daily[idx]
            b_boot = b_daily[idx]
            a_s = self._sharpe(a_boot)
            b_s = self._sharpe(b_boot)
            a_sharpes[i] = a_s
            b_sharpes[i] = b_s
            a_total_returns[i] = float(np.prod(1 + a_boot) - 1)
            b_total_returns[i] = float(np.prod(1 + b_boot) - 1)
            sharpe_diffs[i] = b_s - a_s
            return_diffs[i] = b_total_returns[i] - a_total_returns[i]

        # 3. 配对 t 检验（diff 序列是 i.i.d.）
        sharpe_t, sharpe_p = stats.ttest_1samp(sharpe_diffs, 0.0)
        return_t, return_p = stats.ttest_1samp(return_diffs, 0.0)

        # 4. 百分位 CI
        sharpe_ci = np.percentile(sharpe_diffs, [2.5, 97.5])
        return_ci = np.percentile(return_diffs, [2.5, 97.5])

        # 5. 指标
        a_sharpe_mean = float(np.mean(a_sharpes))
        b_sharpe_mean = float(np.mean(b_sharpes))
        a_return_mean = float(np.mean(a_total_returns))
        b_return_mean = float(np.mean(b_total_returns))
        sharpe_diff = b_sharpe_mean - a_sharpe_mean
        return_diff = b_return_mean - a_return_mean

        # 6. 胜者判定
        if sharpe_diff > 0 and sharpe_p < 0.05:
            winner = "B"
            confidence = "高" if sharpe_p < 0.01 else "中"
        elif sharpe_diff < 0 and sharpe_p < 0.05:
            winner = "A"
            confidence = "高" if sharpe_p < 0.01 else "中"
        else:
            winner = "无显著差异"
            confidence = "低"

        recommendation = self._generate_recommendation(
            winner, confidence, sharpe_diff, return_diff, sharpe_p
        )

        return ABTestResult(
            group_a_name=config.group_a.get("name", "A 组"),
            group_b_name=config.group_b.get("name", "B 组"),
            a_sharpe=round(a_sharpe_mean, 3),
            a_return=round(a_return_mean, 4),
            a_max_drawdown=round(a_result["max_drawdown"], 4),
            a_win_rate=round(a_result["win_rate"], 4),
            a_trades=a_result["trades"],
            b_sharpe=round(b_sharpe_mean, 3),
            b_return=round(b_return_mean, 4),
            b_max_drawdown=round(b_result["max_drawdown"], 4),
            b_win_rate=round(b_result["win_rate"], 4),
            b_trades=b_result["trades"],
            sharpe_p_value=round(float(sharpe_p), 4),
            return_p_value=round(float(return_p), 4),
            sharpe_diff=round(sharpe_diff, 3),
            return_diff=round(return_diff, 4),
            sharpe_ci_lower=round(float(sharpe_ci[0]), 3),
            sharpe_ci_upper=round(float(sharpe_ci[1]), 3),
            return_ci_lower=round(float(return_ci[0]), 4),
            return_ci_upper=round(float(return_ci[1]), 4),
            winner=winner,
            confidence=confidence,
            recommendation=recommendation,
        )

    @staticmethod
    def _sharpe(returns: np.ndarray) -> float:
        """年化 Sharpe（日频）"""
        if len(returns) < 2:
            return 0.0
        std = float(np.std(returns, ddof=1))
        if std == 0:
            return 0.0
        return float(np.mean(returns) / std * np.sqrt(TRADING_DAYS_PER_YEAR))

    def _run_test_degraded(self, config, a_result, b_result) -> ABTestResult:
        """降级路径：引擎无日收益序列时"""
        a_sharpe = a_result["sharpe_ratio"]
        b_sharpe = b_result["sharpe_ratio"]
        a_return = a_result["total_return"]
        b_return = b_result["total_return"]
        sharpe_diff = b_sharpe - a_sharpe
        return_diff = b_return - a_return
        winner = "B" if sharpe_diff > 0 else ("A" if sharpe_diff < 0 else "无显著差异")
        return ABTestResult(
            group_a_name=config.group_a.get("name", "A 组"),
            group_b_name=config.group_b.get("name", "B 组"),
            a_sharpe=a_sharpe, a_return=a_return,
            a_max_drawdown=a_result["max_drawdown"],
            a_win_rate=a_result["win_rate"], a_trades=a_result["trades"],
            b_sharpe=b_sharpe, b_return=b_return,
            b_max_drawdown=b_result["max_drawdown"],
            b_win_rate=b_result["win_rate"], b_trades=b_result["trades"],
            sharpe_p_value=1.0, return_p_value=1.0,
            sharpe_diff=sharpe_diff, return_diff=return_diff,
            sharpe_ci_lower=0.0, sharpe_ci_upper=0.0,
            return_ci_lower=0.0, return_ci_upper=0.0,
            winner=winner, confidence="低",
            recommendation="引擎未返回日收益序列，无法做 bootstrap 配对检验，结论仅供参考",
        )

    def _generate_recommendation(self, winner, confidence, sharpe_diff, return_diff, p_value):
        """生成投资建议"""
        if winner == "无显著差异":
            return "两组策略表现无显著差异，建议选择更简单或交易成本更低的方案。"

        direction = "优于" if winner == "B" else "劣于"
        if confidence == "高":
            return (f"高置信度：B 组策略{direction} A 组 "
                    f"(Sharpe 差异: {sharpe_diff:+.3f}, p={p_value:.4f})。"
                    f"建议采用 B 组参数。")
        else:
            return (f"中等置信度：B 组策略可能{direction} A 组 "
                    f"(Sharpe 差异: {sharpe_diff:+.3f}, p={p_value:.4f})。"
                    f"建议进一步验证。")


# 单例
ab_tester = ABTester()
