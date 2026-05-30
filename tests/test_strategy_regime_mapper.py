"""策略-状态映射测试"""

import pytest


class TestStrategyRegimeMapper:
    def test_trend_up_allocation(self):
        """TREND_UP 所有策略激活"""
        from lib.strategy_regime_mapper import get_strategy_allocation
        alloc = get_strategy_allocation("TREND_UP")
        assert len(alloc.active) == 5
        assert len(alloc.skipped) == 0

    def test_mean_reversion_allocation(self):
        """MEAN_REVERSION 抑制趋势策略"""
        from lib.strategy_regime_mapper import get_strategy_allocation
        alloc = get_strategy_allocation("MEAN_REVERSION")
        assert alloc.weights["量价突破"] < alloc.weights["热门板块"]

    def test_high_volatility_allocation(self):
        """HIGH_VOLATILITY 只有龙虎榜满权重"""
        from lib.strategy_regime_mapper import get_strategy_allocation
        alloc = get_strategy_allocation("HIGH_VOLATILITY")
        assert alloc.weights["龙虎榜"] == 1.0
        assert alloc.weights["量价突破"] < 0.2

    def test_risk_off_allocation(self):
        """RISK_OFF 大部分策略清零"""
        from lib.strategy_regime_mapper import get_strategy_allocation
        alloc = get_strategy_allocation("RISK_OFF")
        assert alloc.weights["涨停板复盘"] == 0.0
        assert alloc.weights["量价突破"] == 0.0
        assert alloc.weights["龙虎榜"] > 0

    def test_skipped_strategies(self):
        """低于阈值的策略被跳过"""
        from lib.strategy_regime_mapper import get_strategy_allocation
        alloc = get_strategy_allocation("RISK_OFF")
        assert "涨停板复盘" in alloc.skipped
        assert "量价突破" in alloc.skipped

    def test_apply_weights_to_candidates(self):
        """权重应用到候选股评分"""
        from lib.strategy_regime_mapper import apply_strategy_weights_to_candidates
        candidates = [
            {"code": "600519", "confidence": 0.8, "综合评分": 7.5},
            {"code": "000858", "confidence": 0.6, "综合评分": 6.0},
        ]
        adjusted = apply_strategy_weights_to_candidates(candidates, "test", 0.5)
        assert adjusted[0]["confidence"] == 0.4
        assert adjusted[0]["综合评分"] == 3.75
        assert adjusted[0]["_strategy_weight"] == 0.5

    def test_apply_weights_full(self):
        """权重=1.0 不修改"""
        from lib.strategy_regime_mapper import apply_strategy_weights_to_candidates
        candidates = [{"confidence": 0.8}]
        adjusted = apply_strategy_weights_to_candidates(candidates, "test", 1.0)
        assert adjusted == candidates

    def test_unknown_regime_fallback(self):
        """未知 regime 回退到等权重"""
        from lib.strategy_regime_mapper import get_strategy_allocation
        alloc = get_strategy_allocation("UNKNOWN")
        assert len(alloc.active) == 5
        assert all(w == 0.5 for w in alloc.weights.values())
