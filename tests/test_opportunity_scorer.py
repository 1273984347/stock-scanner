"""机会评分引擎测试"""
import pytest
from lib.opportunity_scorer import (
    compute_opportunity_score, score_from_signals,
    REGIME_MULTIPLIER, WEIGHTS,
)


class TestComputeOpportunityScore:
    """compute_opportunity_score 测试"""

    def test_high_score_trend_up(self):
        """TREND_UP + 高Alpha + 高板块 → 高分"""
        result = compute_opportunity_score(
            regime="TREND_UP",
            regime_confidence=0.9,
            alpha_posterior=0.7,
            sector_score=85,
            trend_strength=5.0,
            volume_ratio=1.5,
        )
        assert result["score"] >= 80
        assert result["rating"] == "可做"

    def test_medium_score(self):
        """中等条件 → 观察或过滤"""
        result = compute_opportunity_score(
            regime="MEAN_REVERSION",
            regime_confidence=0.7,
            alpha_posterior=0.6,
            sector_score=70,
            trend_strength=3.0,
            volume_ratio=1.3,
        )
        assert 50 <= result["score"] <= 80

    def test_low_score_trend_down(self):
        """TREND_DOWN + 低板块 → 过滤"""
        result = compute_opportunity_score(
            regime="TREND_DOWN",
            regime_confidence=0.8,
            alpha_posterior=0.4,
            sector_score=30,
            trend_strength=-5.0,
            volume_ratio=0.6,
        )
        assert result["score"] < 70
        assert result["rating"] == "过滤"

    def test_risk_off_zero_score(self):
        """RISK_OFF → 市场状态分为 0"""
        result = compute_opportunity_score(
            regime="RISK_OFF",
            regime_confidence=0.9,
            alpha_posterior=0.5,
            sector_score=50,
            trend_strength=0,
            volume_ratio=1.0,
        )
        assert result["breakdown"]["regime_score"] == 0.0

    def test_breakdown_structure(self):
        """返回结果包含完整 breakdown"""
        result = compute_opportunity_score(
            regime="TREND_UP",
            regime_confidence=0.8,
            alpha_posterior=0.6,
            sector_score=70,
            trend_strength=3.0,
            volume_ratio=1.2,
        )
        assert "score" in result
        assert "rating" in result
        assert "breakdown" in result
        assert "weights" in result
        breakdown = result["breakdown"]
        assert "regime_score" in breakdown
        assert "alpha_score" in breakdown
        assert "sector_score" in breakdown
        assert "trend_score" in breakdown
        assert "volume_score" in breakdown

    def test_score_range_0_to_100(self):
        """分数在 0-100 范围内"""
        # 极端高
        result = compute_opportunity_score(
            regime="TREND_UP", regime_confidence=1.0,
            alpha_posterior=1.0, sector_score=100,
            trend_strength=20.0, volume_ratio=5.0,
        )
        assert 0 <= result["score"] <= 100

        # 极端低
        result = compute_opportunity_score(
            regime="RISK_OFF", regime_confidence=0.0,
            alpha_posterior=0.0, sector_score=0,
            trend_strength=-20.0, volume_ratio=0.1,
        )
        assert 0 <= result["score"] <= 100

    def test_weights_sum_to_one(self):
        """权重和为 1.0"""
        total = sum(WEIGHTS.values())
        assert abs(total - 1.0) < 0.01


class TestScoreFromSignals:
    """score_from_signals 测试"""

    def test_with_full_signal_result(self):
        """完整的 signal_result"""
        signal_result = {
            "regime": {"regime": "TREND_UP", "confidence": 0.8},
            "factors": {"trend_strength": 5.0, "volume_ratio": 1.5},
            "data_factors": {"sector_score": 80},
        }
        result = score_from_signals(signal_result, alpha_posterior=0.65)
        assert result["score"] >= 70

    def test_with_empty_signal_result(self):
        """空 signal_result → 0 分"""
        result = score_from_signals({})
        assert result["score"] == 0
        assert result["rating"] == "过滤"

    def test_with_none_signal_result(self):
        """None signal_result → 0 分"""
        result = score_from_signals(None)
        assert result["score"] == 0

    def test_default_alpha_posterior(self):
        """不传 alpha_posterior 时使用默认值 0.5"""
        signal_result = {
            "regime": {"regime": "TREND_UP", "confidence": 0.8},
            "factors": {"trend_strength": 3.0, "volume_ratio": 1.2},
            "data_factors": {"sector_score": 70},
        }
        result = score_from_signals(signal_result)
        assert result["score"] > 0
