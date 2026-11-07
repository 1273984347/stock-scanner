"""V9 资本配置器测试"""
import pytest
from lib.portfolio.capital_allocator import CapitalAllocator, AllocationResult


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

    def test_tiny_alpha_filtered(self):
        """P1-7: 极小权重（< 0.005）才真正跳过，中间值用 sigmoid 软分配

        旧版 weight=0.01 会被硬切 0.05 过滤；新版 sigmoid 软分配但 < 0.005 仍过滤
        """
        allocator = self._make_allocator()
        result = allocator.allocate(
            alpha_weights={"trend": 0.95, "noise": 0.001},
            total_capital=1_000_000,
        )
        types = [a.alpha_type for a in result]
        assert "noise" not in types, "0.001 极小权重应被过滤"

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
        from lib.scoring.alpha_feedback import AlphaFeedback
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
        from lib.scoring.alpha_factory import AlphaFactory
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

    # ========== P1-6 修复测试（2026-06-06）：MAX_SINGLE_ALPHA 缩放 regime_confidence ==========

    def test_max_single_alpha_scales_with_regime_confidence(self):
        """P1-6: regime_confidence 低时，单仓上限应按比例缩放

        旧版 MAX_SINGLE_ALPHA = 0.40 固定，不受 regime_confidence 影响
        新版：effective_max = MAX_SINGLE_ALPHA * regime_confidence
        """
        allocator = self._make_allocator()
        # 高置信度（1.0）→ 上限 0.40
        result_high = allocator.allocate(
            alpha_weights={"trend": 1.0},
            total_capital=1_000_000,
            regime="TREND_UP",
            regime_confidence=1.0,
        )
        # 低置信度（0.3）→ 上限 0.40 * 0.3 = 0.12
        result_low = allocator.allocate(
            alpha_weights={"trend": 1.0},
            total_capital=1_000_000,
            regime="TREND_UP",
            regime_confidence=0.3,
        )
        assert result_high[0].max_position > result_low[0].max_position * 2, (
            f"低置信度应比高置信度上限低很多："
            f"high={result_high[0].max_position}, low={result_low[0].max_position}"
        )
        # 低置信度的 max_position 应接近 0.12 (0.40 * 0.3)
        assert abs(result_low[0].max_position - 0.12) < 0.05

    # ========== P1-7 修复测试（2026-06-06）：MIN_ALLOCATION 软阈值 ==========

    def test_min_allocation_soft_threshold(self):
        """P1-7: 权重在 0.04-0.05 边界不应阶跃到 0

        旧版：weight < 0.05 直接 continue（不分配）
        新版：sigmoid soft threshold → 边界处不阶跃
        """
        allocator = self._make_allocator()
        # 0.04（旧版会跳过）vs 0.05（旧版会通过）
        # 新版：两者都应被分配，但 0.04 权重略小
        result_low = allocator.allocate(
            alpha_weights={"a": 0.04, "b": 0.96},
            total_capital=1_000_000,
        )
        result_high = allocator.allocate(
            alpha_weights={"a": 0.05, "b": 0.95},
            total_capital=1_000_000,
        )
        # 两者都应包含 alpha "a"（不阶跃跳过）
        a_in_low = any(x.alpha_type == "a" for x in result_low)
        a_in_high = any(x.alpha_type == "a" for x in result_high)
        assert a_in_low and a_in_high, "P1-7 边界处不应阶跃跳过 a"
