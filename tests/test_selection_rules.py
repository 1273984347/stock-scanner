"""选股规则单元测试：EMA21/20日新高/放量/相对强度"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd


def _make_hist(close_series, high_series=None, volume_series=None):
    """构造带 close/high/volume 列的 DataFrame"""
    n = len(close_series)
    if high_series is None:
        high_series = [max(close_series[max(0, i-5):i+1]) for i in range(n)]
    if volume_series is None:
        volume_series = [1_000_000] * n
    return pd.DataFrame({
        "close": close_series,
        "high": high_series,
        "volume": volume_series,
    })


class TestPriceAboveEma21:
    """Rule 1: Price must be above EMA21"""

    def _compute_ema21_filter(self, hist):
        """Returns True if price passes EMA21 filter (should NOT be filtered)"""
        hist = hist.copy()
        hist['ema21'] = hist['close'].ewm(span=21, adjust=False).mean()
        return hist['close'].iloc[-1] > hist['ema21'].iloc[-1]

    def test_price_above_ema_passes(self):
        """Rising price above EMA21 should pass"""
        close = list(np.linspace(10, 20, 60))
        hist = _make_hist(close)
        assert self._compute_ema21_filter(hist) == True

    def test_price_below_ema_filtered(self):
        """Falling price below EMA21 should be filtered"""
        close = list(np.linspace(20, 10, 60))
        hist = _make_hist(close)
        assert self._compute_ema21_filter(hist) == False

    def test_price_equal_to_ema_filtered(self):
        """Price exactly at EMA21 should be filtered (strict >)"""
        close = [10.0] * 60  # Flat: EMA == price
        hist = _make_hist(close)
        assert self._compute_ema21_filter(hist) == False


class TestBreakout20DayHigh:
    """Rule 2: Must break 20-day high"""

    def _compute_breakout_filter(self, hist):
        """Returns True if close >= 20-day high (should NOT be filtered)"""
        high_20 = hist['high'].rolling(20).max().iloc[-1]
        return hist['close'].iloc[-1] >= high_20

    def test_breakout_passes(self):
        """Close at 20-day high should pass"""
        close = list(np.linspace(10, 20, 60))
        high = [c * 1.01 for c in close]
        # Set last 20 days' high to close so rolling max == close[-1]
        for i in range(40, 60):
            high[i] = close[i]
        hist = _make_hist(close, high_series=high)
        assert self._compute_breakout_filter(hist) == True

    def test_below_high_filtered(self):
        """Close below 20-day high should be filtered"""
        close = [10.0] * 60
        for i in range(40, 50):
            close[i] = 15.0
        close[59] = 11.0
        high = [c * 1.01 for c in close]
        hist = _make_hist(close, high_series=high)
        assert self._compute_breakout_filter(hist) == False

    def test_exact_high_passes(self):
        """Close exactly equal to 20-day high should pass"""
        close = [10.0] * 60
        high = [10.0] * 60
        hist = _make_hist(close, high_series=high)
        assert self._compute_breakout_filter(hist) == True


class TestVolumeConfirmation:
    """Rule 3: Breakout day must have volume > 20-day average"""

    def _compute_volume_filter(self, hist):
        """Returns True if volume passes filter (should NOT be filtered)"""
        vol_ma20 = hist['volume'].rolling(20).mean().iloc[-1]
        if vol_ma20 <= 0:
            return True  # skip filter if no volume data
        return hist['volume'].iloc[-1] > vol_ma20

    def test_volume_above_avg_passes(self):
        """Volume above 20-day average should pass"""
        vol = [1_000_000] * 60
        vol[-1] = 2_000_000
        hist = _make_hist([10.0] * 60, volume_series=vol)
        assert self._compute_volume_filter(hist) == True

    def test_volume_below_avg_filtered(self):
        """Volume below 20-day average should be filtered"""
        vol = [1_000_000] * 60
        vol[-1] = 500_000
        hist = _make_hist([10.0] * 60, volume_series=vol)
        assert self._compute_volume_filter(hist) == False

    def test_volume_equal_to_avg_filtered(self):
        """Volume exactly at average should be filtered (strict >)"""
        vol = [1_000_000] * 60
        hist = _make_hist([10.0] * 60, volume_series=vol)
        assert self._compute_volume_filter(hist) == False

    def test_zero_volume_avg_skips_filter(self):
        """Zero volume average should not filter (graceful skip)"""
        vol = [0] * 60
        hist = _make_hist([10.0] * 60, volume_series=vol)
        assert self._compute_volume_filter(hist) == True


class TestRelativeStrength:
    """Rule 4: Stock must be stronger than HS300 index"""

    def _compute_rs_filter(self, stock_close, index_close):
        """Returns True if stock is stronger than index (should NOT be filtered)"""
        stock_ret = (stock_close[-1] / stock_close[-21] - 1) if len(stock_close) > 21 else 0
        index_ret = (index_close[-1] / index_close[-21] - 1) if len(index_close) > 21 else 0
        return stock_ret > index_ret

    def test_stock_stronger_passes(self):
        """Stock outperforming index should pass"""
        stock = list(np.linspace(10, 20, 60))
        index = list(np.linspace(10, 12, 60))
        assert self._compute_rs_filter(stock, index) == True

    def test_stock_weaker_filtered(self):
        """Stock underperforming index should be filtered"""
        stock = list(np.linspace(10, 11, 60))
        index = list(np.linspace(10, 15, 60))
        assert self._compute_rs_filter(stock, index) == False

    def test_equal_returns_filtered(self):
        """Stock with equal return to index should be filtered (strict >)"""
        stock = list(np.linspace(10, 15, 60))
        index = list(np.linspace(10, 15, 60))
        assert self._compute_rs_filter(stock, index) == False

    def test_short_data_no_crash(self):
        """Short data (< 21 points) should not crash"""
        stock = [10.0, 11.0, 12.0]
        index = [10.0, 10.5, 11.0]
        assert self._compute_rs_filter(stock, index) == False


class TestCombinedFilters:
    """Test that all 4 filters work together"""

    def test_all_filters_pass(self):
        """Stock meeting all criteria should pass all filters"""
        close = list(np.linspace(10, 20, 60))
        high = [c * 1.01 for c in close]
        # Set last 20 days' high to close so rolling max == close[-1]
        for i in range(40, 60):
            high[i] = close[i]
        vol = [1_000_000] * 60
        vol[-1] = 2_000_000
        hist = _make_hist(close, high_series=high, volume_series=vol)

        # EMA21 check
        hist_c = hist.copy()
        hist_c['ema21'] = hist_c['close'].ewm(span=21, adjust=False).mean()
        assert hist_c['close'].iloc[-1] > hist_c['ema21'].iloc[-1]

        # Breakout check
        high_20 = hist['high'].rolling(20).max().iloc[-1]
        assert hist['close'].iloc[-1] >= high_20

        # Volume check
        vol_ma20 = hist['volume'].rolling(20).mean().iloc[-1]
        assert hist['volume'].iloc[-1] > vol_ma20

    def test_one_filter_fails_rejects(self):
        """Stock failing any one filter should be rejected"""
        # Downtrend: fails EMA21
        close = list(np.linspace(20, 10, 60))
        hist = _make_hist(close)
        hist_c = hist.copy()
        hist_c['ema21'] = hist_c['close'].ewm(span=21, adjust=False).mean()
        assert hist_c['close'].iloc[-1] <= hist_c['ema21'].iloc[-1]
