"""通道指标测试"""
import pytest
import numpy as np
import pandas as pd
from lib.indicators import (
    calculate_atr,
    calculate_keltner_channels,
    calculate_xosi_channels,
    get_channel_position,
    get_channel_signals,
    get_regime_from_kc,
    compute_all_channels,
    KC_LENGTH, KC_ATR, KC_SCALAR,
    XOSI_SHORT, XOSI_LONG,
)


def _make_bar(start: int = 100, n: int = 100) -> pd.DataFrame:
    """生成测试用 OHLCV 数据（简单矩形波，ATR 稳定）"""
    np.random.seed(42)
    dates = pd.bdate_range("2024-01-01", periods=n)
    close = start + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.random.rand(n) * 1.5
    low = close - np.random.rand(n) * 1.5
    open_ = close + (np.random.rand(n) - 0.5) * 0.5
    volume = np.random.randint(1e6, 1e7, n)
    return pd.DataFrame({
        "date": dates, "open": open_, "high": high,
        "low": low, "close": close, "volume": volume,
    }, index=dates)


class TestCalculateATR:
    def test_output_length(self):
        df = _make_bar(n=50)
        atr = calculate_atr(df, period=14)
        assert len(atr) == len(df)
        assert atr.iloc[:13].isna().all()
        assert not atr.iloc[14:].isna().any()

    def test_positive_values(self):
        df = _make_bar(n=50)
        atr = calculate_atr(df, period=14)
        assert (atr.dropna() >= 0).all()


class TestKeltnerChannels:
    def test_bands_order(self):
        df = _make_bar(n=50)
        kc = calculate_keltner_channels(df)
        valid = kc.dropna()
        # 在有效数据段，KC上轨应 >= 中轨 >= 下轨
        assert (valid["kc_upper"] >= valid["kc_mid"]).all()
        assert (valid["kc_mid"] >= valid["kc_lower"]).all()
        # 带宽应为正
        assert (valid["kc_upper"] - valid["kc_lower"] > 0).all()

    def test_insufficient_data(self):
        df = _make_bar(n=10)
        kc = calculate_keltner_channels(df)
        assert kc.empty


class TestXosiChannels:
    def test_bands_order(self):
        df = _make_bar(n=80)
        xosi = calculate_xosi_channels(df)
        valid = xosi.dropna()
        # 在有效数据段，长期通道应宽于短期通道
        assert (valid["xu_long"] >= valid["xu_short"]).all()
        assert (valid["xd_long"] <= valid["xd_short"]).all()

    def test_insufficient_data(self):
        df = _make_bar(n=10)
        xosi = calculate_xosi_channels(df)
        assert xosi.empty


class TestChannelPosition:
    def test_position_bounds(self):
        df = _make_bar(n=80)
        kc = calculate_keltner_channels(df)
        xosi = calculate_xosi_channels(df)
        pos = get_channel_position(df, kc, xosi)
        assert (pos["kc_pos"] >= 0).all()
        assert (pos["kc_pos"] <= 1).all()


class TestChannelSignals:
    def test_no_signal_on_insufficient_data(self):
        df = _make_bar(n=10)
        kc = calculate_keltner_channels(df)
        xosi = calculate_xosi_channels(df)
        sigs = get_channel_signals(df, kc, xosi)
        assert sigs.empty

    def test_signal_has_required_columns(self):
        df = _make_bar(n=80)
        kc = calculate_keltner_channels(df)
        xosi = calculate_xosi_channels(df)
        sigs = get_channel_signals(df, kc, xosi)
        assert "signal" in sigs.columns
        assert "confidence" in sigs.columns
        assert "reason" in sigs.columns


class TestRegimeFromKC:
    def test_returns_dict(self):
        df = _make_bar(n=30)
        kc = calculate_keltner_channels(df)
        regime = get_regime_from_kc(kc)
        assert isinstance(regime, dict)
        assert "trend_score" in regime
        assert "volatility_score" in regime
        assert "compression_score" in regime


class TestComputeAllChannels:
    def test_full_pipeline(self):
        df = _make_bar(n=80)
        result = compute_all_channels(df)
        assert "kc" in result
        assert "xosi" in result
        assert "position" in result
        assert "signals" in result
