"""动态权重管理测试"""

import pytest


class TestDynamicWeightManager:
    def test_compute_weights_trend_up(self):
        """TREND_UP 偏好动量/突破"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager()
        result = mgr.compute_weights("TREND_UP")
        assert result.strategy_weights["trend_breakout"] > result.strategy_weights["mean_reversion"]
        assert result.strategy_weights["momentum"] > 0.15

    def test_compute_weights_mean_reversion(self):
        """MEAN_REVERSION 偏好均值回归"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager()
        result = mgr.compute_weights("MEAN_REVERSION")
        assert result.strategy_weights["mean_reversion"] > result.strategy_weights["trend_breakout"]

    def test_compute_weights_risk_off(self):
        """RISK_OFF 大幅降低趋势/动量权重"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager()
        result = mgr.compute_weights("RISK_OFF")
        # blend_ratio=0.7 时不会完全为零，但应远低于其他策略
        assert result.strategy_weights["trend"] < 0.10
        assert result.strategy_weights["momentum"] < 0.10
        assert result.strategy_weights["volatility"] > result.strategy_weights["trend"]

    def test_weights_sum_to_one(self):
        """权重归一化"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager()
        for regime in ["TREND_UP", "MEAN_REVERSION", "HIGH_VOLATILITY", "TREND_DOWN", "RISK_OFF"]:
            result = mgr.compute_weights(regime)
            total = sum(result.strategy_weights.values())
            assert abs(total - 1.0) < 0.01, f"{regime} weights sum={total}"

    def test_ic_weights_sum_to_one(self):
        """IC 权重归一化"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager()
        result = mgr.compute_weights("TREND_UP")
        total = sum(result.ic_factor_weights.values())
        assert abs(total - 1.0) < 0.01

    def test_regime_field(self):
        """regime 字段正确"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager()
        result = mgr.compute_weights("MEAN_REVERSION")
        assert result.regime == "MEAN_REVERSION"

    def test_unknown_regime(self):
        """未知 regime 使用基准权重"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager()
        result = manager = DynamicWeightManager()
        result = mgr.compute_weights("UNKNOWN_REGIME")
        assert sum(result.strategy_weights.values()) > 0
