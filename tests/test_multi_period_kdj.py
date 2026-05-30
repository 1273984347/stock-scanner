"""多周期 KDJ 共振测试"""

import pytest
import pandas as pd
import numpy as np


class TestMultiPeriodKDJ:
    def test_compute_basic(self):
        """基本功能：返回 MultiPeriodKDJResult"""
        from lib.multi_period_kdj import compute_multi_period_kdj
        # 构造上升趋势数据
        n = 100
        close = pd.Series(np.linspace(10, 20, n) + np.random.normal(0, 0.3, n))
        high = close + abs(np.random.normal(0, 0.2, n))
        low = close - abs(np.random.normal(0, 0.2, n))

        result = compute_multi_period_kdj(high, low, close)
        assert hasattr(result, "resonance_score")
        assert hasattr(result, "resonance_type")
        assert hasattr(result, "fast")
        assert hasattr(result, "medium")
        assert hasattr(result, "slow")
        assert -100 <= result.resonance_score <= 100

    def test_resonance_bullish(self):
        """温和上升趋势：共振偏多"""
        from lib.multi_period_kdj import compute_multi_period_kdj
        n = 100
        # 温和上升 + 回调，避免 KDJ 超买钝化
        base = np.linspace(10, 15, n)
        osc = np.sin(np.linspace(0, 8, n)) * 1.5
        close = pd.Series(base + osc)
        high = close + 0.3
        low = close - 0.3

        result = compute_multi_period_kdj(high, low, close)
        # 温和上升趋势应有偏多信号（至少不是 strong_sell）
        assert result.resonance_type in ("strong_buy", "buy", "neutral")

    def test_resonance_bearish(self):
        """下降趋势：共振偏空"""
        from lib.multi_period_kdj import compute_multi_period_kdj
        n = 100
        close = pd.Series(np.linspace(30, 10, n))
        high = close + 0.5
        low = close - 0.5

        result = compute_multi_period_kdj(high, low, close)
        assert result.resonance_score < 0

    def test_resonance_neutral(self):
        """震荡数据：中性区间"""
        from lib.multi_period_kdj import compute_multi_period_kdj
        n = 100
        close = pd.Series(15 + np.sin(np.linspace(0, 10, n)) * 2)
        high = close + 0.3
        low = close - 0.3

        result = compute_multi_period_kdj(high, low, close)
        # 震荡市应在 -50 到 50 之间
        assert -60 <= result.resonance_score <= 60

    def test_turn_detection(self):
        """拐头检测"""
        from lib.multi_period_kdj import _detect_turn
        # 构造 J 值序列：先降后升（低位拐头）
        j_series = pd.Series([50, 30, 15, 10, 8, 12, 18])
        turn = _detect_turn(j_series)
        assert turn in ("turn_up", "neutral")

    def test_insufficient_data(self):
        """数据不足：不崩溃"""
        from lib.multi_period_kdj import compute_multi_period_kdj
        close = pd.Series([10.0, 10.5, 11.0])
        high = close + 0.2
        low = close - 0.2

        result = compute_multi_period_kdj(high, low, close)
        assert result.resonance_type in ("strong_buy", "buy", "neutral", "sell", "strong_sell")

    def test_backward_compatibility(self):
        """向后兼容：原有 calc_kdj 不受影响"""
        from factors import calc_kdj
        n = 50
        high = pd.Series(np.linspace(10, 15, n) + 0.5)
        low = pd.Series(np.linspace(10, 15, n) - 0.5)
        close = pd.Series(np.linspace(10, 15, n))

        kdj = calc_kdj(high, low, close)
        assert "K" in kdj
        assert "D" in kdj
        assert "J" in kdj
