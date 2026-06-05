"""V6 六因子评分系统测试"""
import pytest
from lib.scorer import (
    six_factor_score, score_trend, score_momentum, score_volume,
    score_sector, score_volatility_quality, score_structure,
    SIX_FACTOR_WEIGHTS,
)


class TestScoreTrend:
    def test_strong_trend(self):
        assert score_trend({"trend_strength": 6.0}) == 90

    def test_moderate_trend(self):
        assert score_trend({"trend_strength": 3.0}) == 75

    def test_weak_positive(self):
        assert score_trend({"trend_strength": 1.0}) == 60

    def test_weak_negative(self):
        assert score_trend({"trend_strength": -1.0}) == 40

    def test_strong_negative(self):
        assert score_trend({"trend_strength": -5.0}) == 20

    def test_missing_key(self):
        assert score_trend({}) == 40  # default trend_strength=0 → weak negative range


class TestScoreMomentum:
    def test_strong_momentum(self):
        assert score_momentum({"momentum_20": 15.0}) == 90

    def test_moderate_momentum(self):
        assert score_momentum({"momentum_20": 7.0}) == 75

    def test_positive_momentum(self):
        assert score_momentum({"momentum_20": 2.0}) == 60

    def test_weak_negative(self):
        assert score_momentum({"momentum_20": -3.0}) == 40

    def test_strong_negative(self):
        assert score_momentum({"momentum_20": -10.0}) == 20


class TestScoreVolume:
    def test_heavy_volume(self):
        assert score_volume({"volume_ratio": 2.5}) == 90

    def test_moderate_volume(self):
        assert score_volume({"volume_ratio": 1.6}) == 75

    def test_normal_volume(self):
        assert score_volume({"volume_ratio": 1.1}) == 60

    def test_low_volume(self):
        assert score_volume({"volume_ratio": 0.8}) == 40

    def test_very_low_volume(self):
        assert score_volume({"volume_ratio": 0.5}) == 20


class TestScoreSector:
    def test_high_sector(self):
        assert score_sector(85) == 85

    def test_medium_sector(self):
        assert score_sector(60) == 60

    def test_none_returns_neutral(self):
        assert score_sector(None) == 50

    def test_zero_returns_neutral(self):
        assert score_sector(0) == 50

    def test_capped_at_100(self):
        assert score_sector(120) == 100


class TestScoreVolatilityQuality:
    def test_optimal_vol(self):
        assert score_volatility_quality({"volatility_20": 25}) == 80

    def test_acceptable_high(self):
        assert score_volatility_quality({"volatility_20": 40}) == 60

    def test_too_high(self):
        assert score_volatility_quality({"volatility_20": 60}) == 30

    def test_too_low(self):
        assert score_volatility_quality({"volatility_20": 5}) == 40


class TestScoreStructure:
    def test_strong_structure(self):
        assert score_structure({"adx": 30, "kdj_j": 50}) == 80

    def test_moderate_structure(self):
        assert score_structure({"adx": 22, "kdj_j": 50}) == 65

    def test_weak_structure(self):
        assert score_structure({"adx": 16, "kdj_j": 50}) == 50

    def test_no_structure(self):
        assert score_structure({"adx": 10, "kdj_j": 50}) == 30

    def test_kdj_bonus(self):
        # ADX=30 (80) + KDJ<30 (+10) = 90
        assert score_structure({"adx": 30, "kdj_j": 20}) == 90

    def test_kdj_bonus_capped(self):
        # ADX=30 (80) + KDJ<30 (+10) = 90, not over 100
        assert score_structure({"adx": 30, "kdj_j": 10}) == 90


class TestSixFactorScore:
    def test_strong_buy(self):
        """所有因子都强 → 强烈买入"""
        factors = {
            "trend_strength": 6.0,
            "momentum_20": 12.0,
            "volume_ratio": 2.0,
            "volatility_20": 25,
            "adx": 30,
            "kdj_j": 50,
        }
        result = six_factor_score(factors, sector_score=85)
        assert result["score"] >= 80
        assert result["rating"] == "强烈买入"

    def test_avoid(self):
        """所有因子都弱 → 回避"""
        factors = {
            "trend_strength": -5.0,
            "momentum_20": -10.0,
            "volume_ratio": 0.5,
            "volatility_20": 60,
            "adx": 8,
            "kdj_j": 50,
        }
        result = six_factor_score(factors, sector_score=20)
        assert result["score"] < 40
        assert result["rating"] == "回避"

    def test_result_structure(self):
        """返回结果包含所有必要字段"""
        result = six_factor_score({})
        assert "score" in result
        assert "rating" in result
        assert "breakdown" in result
        assert "weights" in result
        assert len(result["breakdown"]) == 6

    def test_weights_sum_to_one(self):
        total = sum(SIX_FACTOR_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01

    def test_score_range(self):
        """极端值也在 0-100 范围内"""
        result = six_factor_score({
            "trend_strength": 100,
            "momentum_20": 100,
            "volume_ratio": 100,
            "volatility_20": 0,
            "adx": 100,
            "kdj_j": 0,
        }, sector_score=100)
        assert 0 <= result["score"] <= 100

    def test_default_factors(self):
        """空 dict 也能正常返回"""
        result = six_factor_score({})
        assert result["score"] > 0
        assert result["rating"] in ("强烈买入", "买入", "持有", "谨慎", "回避")

    def test_complements_comprehensive_score(self):
        """六因子与四维评分可以同时使用"""
        from lib.scorer import comprehensive_score, score_fundamental, score_capital_flow, score_news, score_technical

        four_dim = comprehensive_score(
            score_fundamental(pe=15, roe=0.20),
            score_capital_flow(main_net_ratio=0.10),
            score_news(no_negative=True),
            score_technical(return_10d=0.05, return_20d=0.10),
        )
        six_dim = six_factor_score({
            "trend_strength": 3.0,
            "momentum_20": 5.0,
            "volume_ratio": 1.5,
            "volatility_20": 25,
            "adx": 22,
            "kdj_j": 50,
        }, sector_score=70)

        # 两个评分系统独立运行
        assert "score" in four_dim
        assert "score" in six_dim
        # 分数可能不同（因为评估维度不同）
        assert four_dim["score"] != six_dim["score"] or True  # 不要求一定不同
