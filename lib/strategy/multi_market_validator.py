"""多市场周期验证 —— 牛市/熊市/震荡市分别回测

验证策略在不同市场环境下的表现：
- 2019-2020 牛市（结构性行情，科技+消费领涨）
- 2021-2022 熊市（估值收缩，赛道股回调）
- 2023-2024 震荡市（存量博弈，板块轮动快）

使用方式：
    from lib.strategy.multi_market_validator import MultiMarketValidator
    validator = MultiMarketValidator()
    report = validator.validate(symbols=["600519", "000858"])
"""

import logging

# §4.2 P1 修复 (2026-06-08): TRADING_DAYS_PER_YEAR 常量
TRADING_DAYS_PER_YEAR = 252  # A 股年化交易天数 (硬编码, 简化)

from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

# §4.2 P1 修复 (2026-06-08): ROBUST_MAX_DRAWDOWN 常量化
ROBUST_MAX_DRAWDOWN: float = 0.30  # 30% 回撤容忍度


@dataclass
class MarketPeriod:
    """市场周期定义"""
    name: str
    start_date: str
    end_date: str
    description: str
    expected_behavior: str  # 策略应有表现


# 预定义市场周期
MARKET_PERIODS = [
    MarketPeriod(
        name="牛市",
        start_date="2019-01-01",
        end_date="2020-12-31",
        description="结构性行情，科技+消费领涨，创业板涨幅超80%",
        expected_behavior="策略应有正收益，动量因子表现好",
    ),
    MarketPeriod(
        name="熊市",
        start_date="2021-01-01",
        end_date="2022-12-31",
        description="估值收缩，赛道股回调，沪深300跌超30%",
        expected_behavior="策略应控制回撤，止损规则发挥作用",
    ),
    MarketPeriod(
        name="震荡市",
        start_date="2023-01-01",
        end_date="2024-12-31",
        description="存量博弈，板块轮动快，指数窄幅震荡",
        expected_behavior="策略应灵活调仓，板块轮动因子有价值",
    ),
]


class MultiMarketValidator:
    """多市场周期验证器"""

    def __init__(self, periods: List[MarketPeriod] = None):
        self.periods = periods or MARKET_PERIODS

    def validate(self, symbols: List[str], backtest_func=None) -> Dict:
        """在多个市场周期上验证策略

        Args:
            symbols: 股票代码列表
            backtest_func: 回测函数，默认用 run_rebalance_backtest

        Returns:
            dict with keys:
            - periods: list of {name, metrics, trades}
            - summary: 综合评估
            - is_robust: 是否在所有周期都表现可接受
        """
        if backtest_func is None:
            from backtest.rebalance import run_rebalance_backtest
            backtest_func = run_rebalance_backtest

        results = []
        for period in self.periods:
            logger.info(f"验证周期: {period.name} ({period.start_date} ~ {period.end_date})")

            # 计算天数
            start = pd.to_datetime(period.start_date)
            end = pd.to_datetime(period.end_date)
            days = (end - start).days

            # §4.2 P0 #2 修复: 检测"3 周期相同 end_date"导致实际跑同一窗口的情况
            # 已知问题: run_rebalance_backtest 当前不支持 start_date/end_date slicing
            # 修复路径: backtest 引擎 walk-forward 重构 (本次仅警告 + 透传参数)
            all_same_end = all(
                pd.to_datetime(p.end_date) == end for p in self.periods
            )
            if all_same_end:
                logger.warning(
                    f"[multi_market_validator] 所有 {len(self.periods)} 周期 end_date 都是 {period.end_date}, "
                    f"可能实际跑同一窗口 (run_rebalance_backtest 暂不支持 start_date slicing). "
                    f"等 walk-forward 引擎重构"
                )

            try:
                result = backtest_func(
                    symbols=symbols,
                    days=days,
                    disable_loss_breaker=False,
                    # §4.2 P0 #2 修复: 透传 start_date/end_date 给 backtest_func
                    # 当前 run_rebalance_backtest 暂忽略, 但签名就绪等 walk-forward 重构
                    start_date=period.start_date,
                    end_date=period.end_date,
                )

                if result:
                    metrics = result.get("metrics", {})
                    results.append({
                        "period": period.name,
                        "description": period.description,
                        "expected": period.expected_behavior,
                        "trade_count": result.get("trade_count", 0),
                        "win_rate": result.get("win_rate", 0),
                        "sharpe": result.get("final_sharpe", 0),
                        "max_drawdown": result.get("final_drawdown", 0),
                        "total_return": getattr(metrics, "total_return", 0) if hasattr(metrics, "total_return") else 0,
                    })
                else:
                    results.append({
                        "period": period.name,
                        "error": "回测无结果",
                    })
            except Exception as e:
                results.append({
                    "period": period.name,
                    "error": str(e),
                })

        # 综合评估
        valid_results = [r for r in results if "error" not in r]
        is_robust = True
        issues = []

        for r in valid_results:
            if r.get("sharpe", 0) < -1:
                is_robust = False
                issues.append(f"{r['period']}: Sharpe={r['sharpe']:.2f} 过低")
            if abs(r.get("max_drawdown", 0)) > ROBUST_MAX_DRAWDOWN:
                is_robust = False
                issues.append(f"{r['period']}: 回撤={r['max_drawdown']:.1%} 过大")

        summary = {
            "total_periods": len(self.periods),
            "valid_periods": len(valid_results),
            "is_robust": is_robust,
            "issues": issues,
            "avg_sharpe": sum(r.get("sharpe", 0) for r in valid_results) / len(valid_results) if valid_results else 0,
            "avg_win_rate": sum(r.get("win_rate", 0) for r in valid_results) / len(valid_results) if valid_results else 0,
        }

        return {
            "periods": results,
            "summary": summary,
        }

    def validate_with_data(self, symbols: List[str], market_data: Dict[str, pd.DataFrame]) -> Dict:
        """用已有数据验证（避免重复下载）

        Args:
            symbols: 股票代码
            market_data: {symbol: DataFrame} 预加载的市场数据
        """
        results = []

        for period in self.periods:
            start = pd.to_datetime(period.start_date)
            end = pd.to_datetime(period.end_date)

            # 过滤数据
            period_data = {}
            for sym, df in market_data.items():
                if "trade_date" in df.columns:
                    mask = (df["trade_date"] >= start) & (df["trade_date"] <= end)
                    filtered = df[mask]
                    if len(filtered) >= 60:
                        period_data[sym] = filtered

            if not period_data:
                results.append({"period": period.name, "error": "无足够数据"})
                continue

            # 用过滤后的数据跑回测
            from backtest.rebalance import run_rebalance_backtest
            try:
                # 临时替换数据源
                result = self._run_with_data(list(period_data.keys()), period_data)
                results.append({
                    "period": period.name,
                    "data_points": sum(len(df) for df in period_data.values()),
                    **result,
                })
            except Exception as e:
                results.append({"period": period.name, "error": str(e)})

        return {"periods": results}

    def _run_with_data(self, symbols: List[str], data: Dict) -> Dict:
        """用指定数据跑回测"""
        # 简化版：直接计算信号
        from factors import compute_all_signals, compute_ic_weighted_score
        from config import get_config

        cfg = get_config("balanced")
        signals = []

        for sym in symbols:
            df = data.get(sym)
            if df is None or df.empty:
                continue

            result = compute_all_signals(df, factor_config=cfg.factor, code=sym)
            if result:
                ic = result.get("ic_score", 0)
                signals.append({"code": sym, "ic_score": ic})

        return {
            "signals_found": len(signals),
            "avg_ic": sum(s["ic_score"] for s in signals) / len(signals) if signals else 0,
        }
