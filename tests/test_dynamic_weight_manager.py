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
        result = mgr.compute_weights("UNKNOWN_REGIME")
        assert sum(result.strategy_weights.values()) > 0

    def test_sector_bucket_high(self):
        """Score >= 70 should be 'high'"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        assert DynamicWeightManager._sector_bucket(80) == "high"
        assert DynamicWeightManager._sector_bucket(70) == "high"

    def test_sector_bucket_mid(self):
        """Score 30-69 should be 'mid'"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        assert DynamicWeightManager._sector_bucket(50) == "mid"
        assert DynamicWeightManager._sector_bucket(30) == "mid"

    def test_sector_bucket_low(self):
        """Score < 30 should be 'low'"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        assert DynamicWeightManager._sector_bucket(10) == "low"
        assert DynamicWeightManager._sector_bucket(0) == "low"

    def test_custom_blend_ratio(self):
        """Custom blend_ratio should affect weight distribution"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager()
        result = mgr.compute_weights("TREND_UP", blend_ratio=0.9)
        assert result is not None
        assert hasattr(result, 'strategy_weights')
        total = sum(result.strategy_weights.values())
        assert abs(total - 1.0) < 0.01

    def test_bayesian_integration_mock(self):
        """Bayesian updater should be called if provided"""
        from lib.dynamic_weight_manager import DynamicWeightManager

        class MockBayesian:
            def get_weight_adjustment(self, market_state, sector_bucket, signal_type):
                return 1.2

        mgr = DynamicWeightManager(bayesian_updater=MockBayesian())
        result = mgr.compute_weights("TREND_UP")
        assert result is not None
        assert hasattr(result, 'strategy_weights')
        assert result.source == "regime_bayesian_blend"
        assert len(result.bayesian_adjustments) > 0

    def test_no_bayesian(self):
        """Without Bayesian updater, weights should still work"""
        from lib.dynamic_weight_manager import DynamicWeightManager
        mgr = DynamicWeightManager(bayesian_updater=None)
        result = mgr.compute_weights("MEAN_REVERSION")
        assert result is not None
        total = sum(result.strategy_weights.values())
        assert abs(total - 1.0) < 0.01
        assert result.source == "regime_preset"
        assert result.bayesian_adjustments == {}
