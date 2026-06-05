"""V9 资本配置器测试"""
import pytest
from lib.capital_allocator import CapitalAllocator, AllocationResult


class TestCapitalAllocator:
    def _make_allocator(self):
        return CapitalAllocator()

    def test_basic_allocation(self):
        """基本分配"""
        allocator = self._make_allocator()
        result = allocator.allocate(
            alpha_weights={"trend": 0.6, "sentiment": 0.4},
            total_capital=1_000_000,
            regime="TREND_UP",
            regime_confidence=0.8,
        )
        assert len(result) == 2
        assert all(isinstance(a, AllocationResult) for a in result)

    def test_weights_sum_to_one(self):
        """权重归一化"""
        allocator = self._make_allocator()
        result = allocator.allocate(
            alpha_weights={"trend": 0.6, "sentiment": 0.4},
            total_capital=1_000_000,
        )
        total = sum(a.weight for a in result)
        assert abs(total - 1.0) < 0.01

    def test_risk_off_zero_budget(self):
        """RISK_OFF → 零风险预算"""
        allocator = self._make_allocator()
        result = allocator.allocate(
            alpha_weights={"trend": 1.0},
            total_capital=1_000_000,
            regime="RISK_OFF",
            regime_confidence=0.9,
        )
        assert all(a.risk_budget == 0 for a in result)

    def test_trend_up_high_budget(self):
        """TREND_UP → 高风险预算"""
        allocator = self._make_allocator()
        result = allocator.allocate(
            alpha_weights={"trend": 1.0},
            total_capital=1_000_000,
            regime="TREND_UP",
            regime_confidence=0.9,
        )
        assert result[0].risk_budget > 0.5

    def test_mean_reversion_moderate_budget(self):
        """MEAN_REVERSION → 中等风险预算"""
        allocator = self._make_allocator()
        result_trend = allocator.allocate(
            alpha_weights={"trend": 1.0},
            total_capital=1_000_000,
            regime="TREND_UP",
            regime_confidence=0.8,
        )
        result_mr = allocator.allocate(
            alpha_weights={"trend": 1.0},
            total_capital=1_000_000,
            regime="MEAN_REVERSION",
            regime_confidence=0.8,
        )
        assert result_trend[0].risk_budget > result_mr[0].risk_budget

    def test_small_alpha_filtered(self):
        """权重过小的 Alpha 被过滤"""
        allocator = self._make_allocator()
        result = allocator.allocate(
            alpha_weights={"trend": 0.95, "noise": 0.01},
            total_capital=1_000_000,
        )
        types = [a.alpha_type for a in result]
        assert "noise" not in types

    def test_max_single_alpha_cap(self):
        """单个 Alpha 不超过最大仓位"""
        allocator = self._make_allocator()
        result = allocator.allocate(
            alpha_weights={"trend": 1.0},
            total_capital=1_000_000,
            regime="TREND_UP",
            regime_confidence=1.0,
        )
        assert result[0].max_position <= CapitalAllocator.MAX_SINGLE_ALPHA

    def test_empty_weights(self):
        """空权重 → 空结果"""
        allocator = self._make_allocator()
        result = allocator.allocate({}, 1_000_000)
        assert result == []

    def test_zero_capital(self):
        """零资金 → 空结果"""
        allocator = self._make_allocator()
        result = allocator.allocate({"trend": 1.0}, 0)
        assert result == []

    def test_allocate_with_feedback(self):
        """从 AlphaFeedback 分配"""
        from lib.alpha_feedback import AlphaFeedback
        fb = AlphaFeedback()
        for _ in range(10):
            fb.record_trade("trend", 5.0, "TREND_UP", "AI")
        for _ in range(5):
            fb.record_trade("sentiment", -2.0, "TREND_UP", "AI")

        allocator = self._make_allocator()
        result = allocator.allocate_with_feedback(
            fb, 1_000_000, "TREND_UP", 0.8,
        )
        assert len(result) >= 1

    def test_allocate_with_factory(self):
        """从 AlphaFactory 分配"""
        from lib.alpha_factory import AlphaFactory
        factory = AlphaFactory(persist_path=None)
        id1 = factory.discover("momentum_20")
        for _ in range(15):
            factory.record_trade(id1, 5.0)
        factory.update_lifecycle()

        allocator = self._make_allocator()
        result = allocator.allocate_with_factory(
            factory, 1_000_000, "TREND_UP", 0.8,
        )
        assert len(result) >= 1

    def test_get_report(self):
        """报告可读"""
        allocator = self._make_allocator()
        result = allocator.allocate(
            alpha_weights={"trend": 0.6, "sentiment": 0.4},
            total_capital=1_000_000,
        )
        report = allocator.get_report(result)
        assert "资本配置报告" in report
        assert "trend" in report
