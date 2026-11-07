"""机会评分引擎测试"""
import pytest
from lib.scoring.opportunity_scorer import (
    compute_opportunity_score, score_from_signals,
    REGIME_MULTIPLIER, WEIGHTS,
    set_opp_score_weights, get_opp_score_weights, reset_opp_score_weights,
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


class TestConfigDrivenWeights:
    """P2-1: 权重应可从 config 覆盖，而非硬编码"""

    def setup_method(self):
        """每个测试前 reset，避免污染其他测试"""
        reset_opp_score_weights()

    def teardown_method(self):
        reset_opp_score_weights()

    def test_get_opp_score_weights_returns_default(self):
        """未设置时返回默认 5 项权重"""
        weights = get_opp_score_weights()
        assert set(weights.keys()) == {"regime", "alpha", "sector", "trend", "volume"}
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_set_opp_score_weights_overrides(self):
        """set_opp_score_weights 后，compute_opportunity_score 用新权重"""
        custom = {
            "regime": 0.50,
            "alpha": 0.20,
            "sector": 0.10,
            "trend": 0.10,
            "volume": 0.10,
        }
        set_opp_score_weights(custom)
        assert get_opp_score_weights() == custom

    def test_score_changes_with_custom_weights(self):
        """红：相同输入 + 不同权重 → 不同分数（验证权重真的生效）"""
        signal_result = {
            "regime": {"regime": "TREND_UP", "confidence": 1.0},
            "factors": {"trend_strength": 0.0, "volume_ratio": 1.0},
            "data_factors": {"sector_score": 50},
        }
        # 走默认权重
        s1 = compute_opportunity_score(
            regime="TREND_UP", regime_confidence=1.0,
            alpha_posterior=0.5, sector_score=50,
            trend_strength=0.0, volume_ratio=1.0,
        )
        # 把 alpha 权重拉满、regime 降为 0
        set_opp_score_weights({
            "regime": 0.0, "alpha": 1.0, "sector": 0.0,
            "trend": 0.0, "volume": 0.0,
        })
        s2 = compute_opportunity_score(
            regime="TREND_UP", regime_confidence=1.0,
            alpha_posterior=0.5, sector_score=50,
            trend_strength=0.0, volume_ratio=1.0,
        )
        # regime 权重 0 后总分应小于默认
        assert s1["score"] > s2["score"], \
            f"权重切换后分数应变化: default={s1['score']} custom={s2['score']}"
        # weights 字段也反映当前权重
        assert s2["weights"]["alpha"] == 1.0

    def test_load_from_config_module(self):
        """从 config 模块加载权重（验证集成点）"""
        from config import FactorConfig
        cfg = FactorConfig()
        weights = get_opp_score_weights(source_config=cfg)
        assert abs(sum(weights.values()) - 1.0) < 0.01
        # 自定义 FactorConfig 的 opp_score_weights 应被读取
        cfg.opp_score_weights = {
            "regime": 0.4, "alpha": 0.3, "sector": 0.1,
            "trend": 0.1, "volume": 0.1,
        }
        weights2 = get_opp_score_weights(source_config=cfg)
        assert weights2 == cfg.opp_score_weights
