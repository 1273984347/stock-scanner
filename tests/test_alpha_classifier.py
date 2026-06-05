"""Alpha 来源分类器测试"""
import pytest
from lib.alpha_classifier import (
    classify_alpha_source, classify_trade_historical,
    AlphaClassification, ALPHA_TYPES,
)


class TestClassifyAlphaSource:
    """classify_alpha_source 测试"""

    def test_trend_alpha(self):
        """TREND_UP + 强趋势 + 正动量 → trend"""
        result = classify_alpha_source(
            regime="TREND_UP",
            factors={"trend_strength": 5.0, "momentum_20": 8.0, "rsi_14": 55},
            channel_filter={"kc_pos": 0.6},
            data_factors={"sector_score": 50},
        )
        assert result.alpha_type == "trend"
        assert "trend" in result.alpha_types
        assert result.confidence > 0.5

    def test_sector_rotation_alpha(self):
        """高板块评分 → sector_rotation"""
        result = classify_alpha_source(
            regime="MEAN_REVERSION",
            factors={"trend_strength": 1.0, "momentum_20": 2.0, "rsi_14": 50},
            channel_filter={"kc_pos": 0.5},
            data_factors={"sector_score": 85},
        )
        assert "sector_rotation" in result.alpha_types

    def test_sentiment_alpha(self):
        """高市场情绪 → sentiment"""
        result = classify_alpha_source(
            regime="TREND_UP",
            factors={"trend_strength": 1.0, "momentum_20": 1.0, "rsi_14": 50},
            channel_filter={"kc_pos": 0.5},
            data_factors={
                "sector_score": 50,
                "market_sentiment": {"sentiment_score": 0.5, "zt_count": 10, "up_down_ratio": 1.5},
            },
        )
        assert "sentiment" in result.alpha_types

    def test_mean_reversion_alpha(self):
        """超卖 + MEAN_REVERSION → mean_reversion"""
        result = classify_alpha_source(
            regime="MEAN_REVERSION",
            factors={"trend_strength": -2.0, "momentum_20": -5.0, "rsi_14": 25},
            channel_filter={"kc_pos": 0.15},
            data_factors={"sector_score": 50},
        )
        assert "mean_reversion" in result.alpha_types

    def test_event_driven_alpha(self):
        """涨停潮 → event_driven"""
        result = classify_alpha_source(
            regime="TREND_UP",
            factors={"trend_strength": 1.0, "momentum_20": 1.0, "rsi_14": 50},
            channel_filter={"kc_pos": 0.5},
            data_factors={
                "sector_score": 50,
                "market_sentiment": {"sentiment_score": 0.2, "zt_count": 45, "up_down_ratio": 1.2},
            },
        )
        assert "event_driven" in result.alpha_types

    def test_multiple_alpha_types(self):
        """同时满足多个条件 → 多个 Alpha 类型"""
        result = classify_alpha_source(
            regime="TREND_UP",
            factors={"trend_strength": 5.0, "momentum_20": 8.0, "rsi_14": 55},
            channel_filter={"kc_pos": 0.6},
            data_factors={
                "sector_score": 85,
                "market_sentiment": {"sentiment_score": 0.5, "zt_count": 10, "up_down_ratio": 1.5},
            },
        )
        assert len(result.alpha_types) >= 2
        assert result.confidence > 0.6

    def test_no_match_defaults_to_unknown(self):
        """无匹配条件 → unknown"""
        result = classify_alpha_source(
            regime="TREND_DOWN",
            factors={"trend_strength": -5.0, "momentum_20": -10.0, "rsi_14": 50},
            channel_filter={"kc_pos": 0.5},
            data_factors={"sector_score": 30},
            combined_signal={"signal": "bearish"},
        )
        assert result.alpha_type == "unknown"

    def test_no_match_bullish_defaults_to_trend(self):
        """无明确匹配但信号看多 → 默认 trend"""
        result = classify_alpha_source(
            regime="LOW_VOLATILITY",
            factors={"trend_strength": 0.5, "momentum_20": 1.0, "rsi_14": 50},
            channel_filter={"kc_pos": 0.5},
            data_factors={"sector_score": 50},
            combined_signal={"signal": "bullish"},
        )
        assert result.alpha_type == "trend"
        assert result.confidence == 0.3

    def test_result_structure(self):
        """返回结果包含所有必要字段"""
        result = classify_alpha_source(
            regime="TREND_UP",
            factors={},
            channel_filter={},
            data_factors={},
        )
        assert isinstance(result, AlphaClassification)
        assert hasattr(result, "alpha_type")
        assert hasattr(result, "alpha_types")
        assert hasattr(result, "confidence")
        assert hasattr(result, "reason")


class TestClassifyTradeHistorical:
    """classify_trade_historical 测试"""

    def test_basic_classification(self):
        """基本分类功能"""
        result = classify_trade_historical(
            trade_date="2024-01-15",
            code="600519",
            regime="TREND_UP",
            sector="白酒",
        )
        assert isinstance(result, AlphaClassification)
        assert result.alpha_type in ALPHA_TYPES or result.alpha_type == "unknown"

    def test_with_hist_data(self):
        """有历史数据时能计算因子"""
        import pandas as pd
        import numpy as np
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=60)
        close = np.linspace(100, 120, 60) + np.random.randn(60) * 2
        df = pd.DataFrame({
            "date": dates,
            "close": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "volume": np.random.randint(1000, 5000, 60),
        })
        result = classify_trade_historical(
            trade_date="2024-03-01",
            code="600519",
            hist_df=df,
            regime="TREND_UP",
            sector="白酒",
        )
        assert result.alpha_type != "unknown" or result.confidence > 0


class TestAlphaTypes:
    """ALPHA_TYPES 常量测试"""

    def test_five_types_defined(self):
        """5 种 Alpha 类型全部定义"""
        assert len(ALPHA_TYPES) == 5
        expected = {"trend", "sector_rotation", "sentiment", "mean_reversion", "event_driven"}
        assert set(ALPHA_TYPES.keys()) == expected
