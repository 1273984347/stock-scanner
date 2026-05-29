"""统一市场体制系统测试 — 验证 RISK_OFF 状态 + 五态模型完整性"""
import pytest
import pandas as pd
import numpy as np
from lib.market_regime import (
    detect_market_regime,
    RISK_OFF, TREND_UP, TREND_DOWN,
    COMPRESSION, MEAN_REVERSION,
    REGIME_STATES, REGIME_POSITION_SCALAR,
    get_regime_description,
)


def _make_index_df(n=60, trend="flat"):
    """生成测试用指数数据"""
    dates = pd.date_range("2024-01-01", periods=n)
    if trend == "up":
        close = np.linspace(3000, 3500, n)
    elif trend == "down":
        close = np.linspace(3500, 3000, n)
    elif trend == "volatile":
        np.random.seed(42)
        close = 3000 + np.cumsum(np.random.randn(n) * 100)
    else:
        close = np.full(n, 3200.0)
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame({"date": dates, "high": high, "low": low, "close": close})


class TestRiskOff:
    """RISK_OFF 状态检测测试"""

    def test_risk_off_constant_exists(self):
        """RISK_OFF 常量已定义"""
        assert RISK_OFF == "RISK_OFF"

    def test_risk_off_in_regime_states(self):
        """RISK_OFF 在 REGIME_STATES 列表中"""
        assert RISK_OFF in REGIME_STATES

    def test_risk_off_position_scalar_zero(self):
        """RISK_OFF 仓位系数为 (0, 0)"""
        assert REGIME_POSITION_SCALAR[RISK_OFF] == (0.0, 0.0)

    def test_all_regimes_have_position_scalar(self):
        """所有状态都有仓位系数定义"""
        for state in REGIME_STATES:
            assert state in REGIME_POSITION_SCALAR, f"{state} 缺少仓位系数"

    def test_all_regimes_have_description(self):
        """所有状态都有描述"""
        for state in REGIME_STATES:
            desc = get_regime_description(state)
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_high_volatility_triggers_risk_off(self):
        """极端波动应触发 RISK_OFF 或其他保护性状态"""
        df = _make_index_df(60, "volatile")
        # 人为制造极端波动
        df.loc[df.index[-5:], "high"] = df["close"].iloc[-5:] * 1.10
        df.loc[df.index[-5:], "low"] = df["close"].iloc[-5:] * 0.90
        result = detect_market_regime(df)
        # 应为保护性状态之一
        assert result["regime"] in (RISK_OFF, COMPRESSION, TREND_DOWN)

    def test_risk_off_returns_zero_position_scalar(self):
        """RISK_OFF 状态返回的 position_scalar 为 (0, 0)"""
        df = _make_index_df(60, "volatile")
        df.loc[df.index[-5:], "high"] = df["close"].iloc[-5:] * 1.10
        df.loc[df.index[-5:], "low"] = df["close"].iloc[-5:] * 0.90
        result = detect_market_regime(df)
        if result["regime"] == RISK_OFF:
            assert result["position_scalar"] == (0.0, 0.0)


class TestRegimeDetection:
    """基础市场状态检测测试"""

    def test_uptrend(self):
        """上升趋势应检测为 TREND_UP 或 MEAN_REVERSION"""
        df = _make_index_df(60, "up")
        result = detect_market_regime(df)
        assert result["regime"] in (TREND_UP, MEAN_REVERSION)

    def test_downtrend(self):
        """下降趋势应检测为 TREND_DOWN 或 MEAN_REVERSION"""
        df = _make_index_df(60, "down")
        result = detect_market_regime(df)
        assert result["regime"] in (TREND_DOWN, MEAN_REVERSION)

    def test_insufficient_data_returns_default(self):
        """数据不足时返回默认状态"""
        df = _make_index_df(10)
        result = detect_market_regime(df)
        assert result["regime"] == MEAN_REVERSION
        assert result["confidence"] == 0.3

    def test_result_structure(self):
        """返回结果包含所有必要字段"""
        df = _make_index_df(60, "up")
        result = detect_market_regime(df)
        assert "regime" in result
        assert "confidence" in result
        assert "indicators" in result
        assert "position_scalar" in result
        assert "atr_ratio" in result
        assert "compression" in result

    def test_confidence_range(self):
        """置信度在 0-1 范围内"""
        df = _make_index_df(60, "up")
        result = detect_market_regime(df)
        assert 0 <= result["confidence"] <= 1

    def test_position_scalar_is_tuple(self):
        """position_scalar 是长度为 2 的 tuple"""
        df = _make_index_df(60, "up")
        result = detect_market_regime(df)
        assert isinstance(result["position_scalar"], tuple)
        assert len(result["position_scalar"]) == 2
        assert result["position_scalar"][0] <= result["position_scalar"][1]


class TestFiveStateCompleteness:
    """五态模型完整性测试"""

    def test_five_states_defined(self):
        """五种状态全部定义"""
        assert len(REGIME_STATES) == 5
        expected = {TREND_UP, MEAN_REVERSION, COMPRESSION, TREND_DOWN, RISK_OFF}
        assert set(REGIME_STATES) == expected

    def test_five_position_scalars(self):
        """五种状态都有仓位系数"""
        assert len(REGIME_POSITION_SCALAR) == 5

    def test_five_descriptions(self):
        """五种状态都有描述"""
        for state in REGIME_STATES:
            desc = get_regime_description(state)
            assert "未知" not in desc, f"{state} 描述不应包含'未知'"
