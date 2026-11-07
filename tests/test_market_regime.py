"""市场状态检测器测试"""
import pytest
import numpy as np
import pandas as pd
import lib.monitoring.market_regime as mr_module
from lib.monitoring.market_regime import (
    detect_market_regime,
    get_regime_description,
    REGIME_STATES,
    REGIME_POSITION_SCALAR,
    TREND_UP, MEAN_REVERSION, TREND_DOWN,
    HIGH_VOLATILITY, LOW_VOLATILITY,
)


def _make_trend_index(n: int = 60, trend: str = "up") -> pd.DataFrame:
    """生成测试用指数数据"""
    np.random.seed(42)
    dates = pd.bdate_range("2024-01-01", periods=n)

    if trend == "up":
        close = 3000 + np.cumsum(np.random.randn(n) * 5 + 3)
    elif trend == "down":
        close = 3000 + np.cumsum(np.random.randn(n) * 5 - 3)
    elif trend == "flat":
        close = 3000 + np.cumsum(np.random.randn(n) * 2)
    else:
        close = 3000 + np.cumsum(np.random.randn(n) * 3)

    high = close + np.abs(np.random.randn(n)) * 10
    low = close - np.abs(np.random.randn(n)) * 10
    return pd.DataFrame({"high": high, "low": low, "close": close}, index=dates)


class TestDetectMarketRegime:
    def test_trend_up(self):
        df = _make_trend_index(n=60, trend="up")
        result = detect_market_regime(df)
        assert result["regime"] in REGIME_STATES
        assert 0 <= result["confidence"] <= 1

    def test_trend_down(self):
        df = _make_trend_index(n=60, trend="down")
        result = detect_market_regime(df)
        assert result["regime"] in REGIME_STATES

    def test_insufficient_data(self):
        df = _make_trend_index(n=10)
        result = detect_market_regime(df)
        assert result["regime"] in REGIME_STATES

    def test_position_scalar(self):
        df = _make_trend_index(n=60, trend="up")
        result = detect_market_regime(df)
        assert "position_scalar" in result
        assert isinstance(result["position_scalar"], tuple)
        assert len(result["position_scalar"]) == 2


class TestRegimeDescription:
    def test_all_states_have_description(self):
        for state in REGIME_STATES:
            desc = get_regime_description(state)
            assert isinstance(desc, str)
            assert len(desc) > 0


class TestRegimePositionScalar:
    def test_position_scalars(self):
        assert REGIME_POSITION_SCALAR[TREND_UP] == (0.75, 1.00)
        assert REGIME_POSITION_SCALAR[MEAN_REVERSION] == (0.25, 0.50)
        assert REGIME_POSITION_SCALAR[HIGH_VOLATILITY] == (0.00, 0.25)
        assert REGIME_POSITION_SCALAR[LOW_VOLATILITY] == (0.50, 0.75)
        assert REGIME_POSITION_SCALAR[TREND_DOWN] == (0.00, 0.00)


class TestCompressionAliasRemoved:
    """P1-1: COMPRESSION 别名反语义（误映射到 HIGH_VOLATILITY）已删除

    原 bug: ``COMPRESSION = HIGH_VOLATILITY`` —— 字面"压缩"应对应 LOW_VOLATILITY。
    修复: 直接删除别名，旧代码点应改用 LOW_VOLATILITY。
    """

    def test_compression_not_exported(self):
        """COMPRESSION 常量不应再从 market_regime 导出"""
        assert not hasattr(mr_module, "COMPRESSION"), (
            "COMPRESSION 别名反语义（压缩 ≠ 高波动），应删除并改用 LOW_VOLATILITY"
        )

    def test_regime_states_contains_six_canonical(self):
        """REGIME_STATES 仅包含六态，无 COMPRESSION 别名"""
        assert "COMPRESSION" not in REGIME_STATES
        assert set(REGIME_STATES) == {
            TREND_UP, MEAN_REVERSION, HIGH_VOLATILITY, LOW_VOLATILITY, TREND_DOWN, "RISK_OFF"
        }
