"""P0-2: factor_optimizer.evaluate_fitness 应内嵌 OOS sharpe 防过拟合

问题: evaluate_fitness 在 in-sample 上做 5000+ 次 fitness 评估，
末尾 walk_forward 只输出报告不进 fitness，过拟合检测形同虚设。

修复: 引入 OOS_SHARPE_WEIGHT 常量 + oos_test_days 参数，
在 fitness 公式中加入 OOS sharpe 权重。
"""
from unittest.mock import patch, MagicMock

import numpy as np
import pytest


class TestEvaluateFitnessOOSSharpe:
    """P0-2: evaluate_fitness 应在 fitness 公式中惩罚 IS/OOS 偏离"""

    def test_oos_sharpe_weight_constant_exists(self):
        """P0-2 红: 应有 OOS_SHARPE_WEIGHT 常量"""
        from lib.scoring.factor_optimizer import OOS_SHARPE_WEIGHT
        assert isinstance(OOS_SHARPE_WEIGHT, (int, float))
        assert 0.0 < OOS_SHARPE_WEIGHT <= 1.0

    def test_oos_sharpe_in_fitness_penalizes_overfit(self):
        """P0-2 红: IS 高分 + OOS 低分 → 总 fitness 应被 OOS 拉低（避免过拟合）"""
        from lib.scoring.factor_optimizer import evaluate_fitness, OOS_SHARPE_WEIGHT

        # 模拟：IS 跑得好，OOS 跑得差 → 应当 fitness 偏低
        call_count = {"n": 0}

        def mock_backtest(symbols, days, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # IS 调用：高 sharpe
                return {
                    "sharpe": 3.0,
                    "max_drawdown": 0.10,
                    "win_rate": 0.6,
                    "n_trades": 50,
                    "total_return": 0.5,
                }
            else:
                # OOS 调用：低 sharpe（过拟合的典型表现）
                return {
                    "sharpe": 0.2,
                    "max_drawdown": 0.10,
                    "win_rate": 0.4,
                    "n_trades": 10,
                    "total_return": 0.05,
                }

        weights = {"volume_ratio": 0.2, "adx": 0.2, "williams_r": 0.1,
                   "cci": 0.1, "rsi_14": 0.1, "trend_strength": 0.1,
                   "kdj_j": 0.1, "momentum_20": 0.05, "volatility_20": 0.05}

        with patch("backtest.rebalance.run_rebalance_backtest", side_effect=mock_backtest):
            fitness, metrics = evaluate_fitness(
                weights=weights,
                symbols=["000001"],
                days=625,
                oos_test_days=125,  # 触发 OOS 评估
            )

        # 关键断言：metrics 应包含 oos_sharpe 字段
        assert "oos_sharpe" in metrics, "metrics 应暴露 oos_sharpe"
        assert metrics["oos_sharpe"] == 0.2

        # 关键断言：调用了 2 次（IS + OOS）
        assert call_count["n"] == 2, f"应调用 2 次 backtest（IS+OOS），实际 {call_count['n']}"

    def test_no_oos_when_oos_test_days_zero(self):
        """P0-2: oos_test_days=0 时不应触发 OOS 调用（backward compat）"""
        from lib.scoring.factor_optimizer import evaluate_fitness

        call_count = {"n": 0}

        def mock_backtest(symbols, days, **kwargs):
            call_count["n"] += 1
            return {
                "sharpe": 1.5,
                "max_drawdown": 0.10,
                "win_rate": 0.5,
                "n_trades": 30,
                "total_return": 0.3,
            }

        weights = {"volume_ratio": 0.2, "adx": 0.2, "williams_r": 0.1,
                   "cci": 0.1, "rsi_14": 0.1, "trend_strength": 0.1,
                   "kdj_j": 0.1, "momentum_20": 0.05, "volatility_20": 0.05}

        with patch("backtest.rebalance.run_rebalance_backtest", side_effect=mock_backtest):
            fitness, metrics = evaluate_fitness(
                weights=weights,
                symbols=["000001"],
                days=500,
                oos_test_days=0,  # 显式禁用 OOS
            )

        # 只调用 1 次（IS only）
        assert call_count["n"] == 1
        # OOS 字段不应出现
        assert "oos_sharpe" not in metrics

    def test_fitness_lower_when_oos_underperforms(self):
        """P0-2: 对比测试 —— OOS 表现差时 fitness 应低于 OOS 表现好时"""
        from lib.scoring.factor_optimizer import evaluate_fitness

        weights = {"volume_ratio": 0.2, "adx": 0.2, "williams_r": 0.1,
                   "cci": 0.1, "rsi_14": 0.1, "trend_strength": 0.1,
                   "kdj_j": 0.1, "momentum_20": 0.05, "volatility_20": 0.05}

        def make_backtest(is_sharpe, oos_sharpe):
            """生成 mock backtest，IS/OOS 表现可调"""
            n = {"c": 0}

            def bt(symbols, days, **kwargs):
                n["c"] += 1
                s = is_sharpe if n["c"] == 1 else oos_sharpe
                return {
                    "sharpe": s,
                    "max_drawdown": 0.10,
                    "win_rate": 0.5,
                    "n_trades": 30,
                    "total_return": 0.3,
                }
            return bt

        # 场景 A：IS 高分 + OOS 低分（过拟合）
        with patch("backtest.rebalance.run_rebalance_backtest",
                   side_effect=make_backtest(is_sharpe=2.0, oos_sharpe=0.1)):
            fitness_overfit, _ = evaluate_fitness(
                weights, ["000001"], days=625, oos_test_days=125,
            )

        # 场景 B：IS 高分 + OOS 高分（健壮）
        with patch("backtest.rebalance.run_rebalance_backtest",
                   side_effect=make_backtest(is_sharpe=2.0, oos_sharpe=1.8)):
            fitness_robust, _ = evaluate_fitness(
                weights, ["000001"], days=625, oos_test_days=125,
            )

        # 关键断言：健壮配置 fitness > 过拟合配置
        assert fitness_robust > fitness_overfit, (
            f"OOS 表现差时 fitness 应被拉低；"
            f"过拟合: {fitness_overfit:.4f}, 健壮: {fitness_robust:.4f}"
        )

    def test_oos_sharpe_none_handled_gracefully(self):
        """P0-2: OOS backtest 返回 None 时不应让 fitness 整体崩溃"""
        from lib.scoring.factor_optimizer import evaluate_fitness

        call_count = {"n": 0}

        def mock_backtest(symbols, days, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # IS 正常
                return {
                    "sharpe": 1.5,
                    "max_drawdown": 0.10,
                    "win_rate": 0.5,
                    "n_trades": 30,
                    "total_return": 0.3,
                }
            else:
                # OOS 返回 None（如数据不足）
                return None

        weights = {"volume_ratio": 0.2, "adx": 0.2, "williams_r": 0.1,
                   "cci": 0.1, "rsi_14": 0.1, "trend_strength": 0.1,
                   "kdj_j": 0.1, "momentum_20": 0.05, "volatility_20": 0.05}

        with patch("backtest.rebalance.run_rebalance_backtest", side_effect=mock_backtest):
            # 不应抛异常
            fitness, metrics = evaluate_fitness(
                weights, ["000001"], days=625, oos_test_days=125,
            )

        # 应当 fallback：OOS sharpe=0, fitness 仅为 IS 部分
        assert "oos_sharpe" in metrics
        assert metrics["oos_sharpe"] == 0.0 or metrics["oos_sharpe"] is None or metrics.get("oos_failed") is True
