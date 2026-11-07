"""统一市场体制系统测试 — 验证 RISK_OFF 状态 + 五态模型完整性"""
import pytest
import pandas as pd
import numpy as np
from lib.monitoring.market_regime import (
    detect_market_regime,
    RISK_OFF, TREND_UP, TREND_DOWN,
    HIGH_VOLATILITY, LOW_VOLATILITY,
    MEAN_REVERSION,
    REGIME_STATES, REGIME_POSITION_SCALAR,
    get_regime_description,
    reset_hysteresis,
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
        assert result["regime"] in (RISK_OFF, HIGH_VOLATILITY, TREND_DOWN)

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
        """数据不足时返回默认状态（HIGH_VOLATILITY 更安全）"""
        reset_hysteresis()  # 重置缓存，确保默认状态为 HIGH_VOLATILITY
        df = _make_index_df(10)
        result = detect_market_regime(df)
        assert result["regime"] == HIGH_VOLATILITY
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
        """六种状态全部定义"""
        assert len(REGIME_STATES) == 6
        expected = {TREND_UP, MEAN_REVERSION, HIGH_VOLATILITY, LOW_VOLATILITY, TREND_DOWN, RISK_OFF}
        assert set(REGIME_STATES) == expected

    def test_five_position_scalars(self):
        """六种状态都有仓位系数"""
        assert len(REGIME_POSITION_SCALAR) == 6

    def test_five_descriptions(self):
        """五种状态都有描述"""
        for state in REGIME_STATES:
            desc = get_regime_description(state)
            assert "未知" not in desc, f"{state} 描述不应包含'未知'"


class TestHysteresis:
    """迟滞机制测试"""

    def test_hysteresis_requires_n_confirmations(self):
        """状态切换需要连续 N 天确认"""
        reset_hysteresis()
        df_up = _make_index_df(60, "up")
        df_down = _make_index_df(60, "down")

        # 第一次调用：建立初始状态
        result1 = detect_market_regime(df_up, use_hysteresis=True, hysteresis_days=3)
        initial_regime = result1["regime"]

        # 第二次调用：切换到不同状态，但只有 1 次确认
        result2 = detect_market_regime(df_down, use_hysteresis=True, hysteresis_days=3)

        # 如果 up 和 down 产生了不同状态，应该有 hysteresis_pending 标记
        raw_down = detect_market_regime(df_down, use_hysteresis=False)
        if raw_down["regime"] != initial_regime:
            # 新状态需要 3 次确认，当前只有 1 次，应保持原状态
            assert result2["regime"] == initial_regime
            assert result2["indicators"].get("hysteresis_pending") is True
            assert result2["indicators"].get("hysteresis_count") == 1

    def test_hysteresis_switches_after_n_confirmations(self):
        """连续 N 次确认后状态切换"""
        reset_hysteresis()
        df_up = _make_index_df(60, "up")
        df_down = _make_index_df(60, "down")

        # 建立初始状态
        detect_market_regime(df_up, use_hysteresis=True, hysteresis_days=3)

        # 连续 3 次确认新状态
        for _ in range(3):
            result = detect_market_regime(df_down, use_hysteresis=True, hysteresis_days=3)

        # 第 3 次确认后应该切换
        raw_down = detect_market_regime(df_down, use_hysteresis=False)
        assert result["regime"] == raw_down["regime"]

    def test_hysteresis_disabled_passthrough(self):
        """use_hysteresis=False 时直接返回当前检测结果"""
        reset_hysteresis()
        df = _make_index_df(60, "up")
        result = detect_market_regime(df, use_hysteresis=False)
        # 应直接返回检测结果，无 hysteresis_pending 标记
        assert "hysteresis_pending" not in result.get("indicators", {})

    def test_reset_hysteresis(self):
        """reset_hysteresis 重置所有状态"""
        reset_hysteresis()
        df = _make_index_df(60, "up")
        detect_market_regime(df, use_hysteresis=True, hysteresis_days=3)
        reset_hysteresis()
        # 重置后默认状态应为 HIGH_VOLATILITY
        result = detect_market_regime(_make_index_df(10), use_hysteresis=False)
        assert result["regime"] == HIGH_VOLATILITY
