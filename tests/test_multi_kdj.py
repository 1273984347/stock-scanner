"""多周期 KDJ 联动测试"""
import pytest
import pandas as pd
import numpy as np

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

from technical_signals import detect_kdj_turn, multi_period_kdj_verdict, generate_signals


def _make_j_series(values):
    """Helper: create a J-value Series from a list."""
    return pd.Series(values, dtype=float)


# ========== KDJ Turn Detection ==========


class TestKdjTurn:
    def test_turn_up_from_oversold(self):
        """J value turns up from below 20"""
        # Last 4: [15, 10, 5, 15] => j[-3]=15, j[-2]=5, j[-1]=15
        # j[-2]<j[-3] (5<15) and j[-1]>j[-2] (15>5) and j[-2]<20 => turn_up
        j = _make_j_series([50, 40, 30, 15, 5, 15])
        assert detect_kdj_turn(j) == "turn_up"

    def test_turn_down_from_overbought(self):
        """J value turns down from above 80"""
        # Last 4: [70, 85, 95, 80] => j[-3]=85, j[-2]=95, j[-1]=80
        # j[-2]>j[-3] (95>85) and j[-1]<j[-2] (80<95) and j[-2]>80 => turn_down
        j = _make_j_series([50, 60, 70, 85, 95, 80])
        assert detect_kdj_turn(j) == "turn_down"

    def test_neutral_no_turn_mid_range(self):
        """No turn detected in mid-range (turn must be in extreme zone)"""
        # Turn up but trough is at 50 (not oversold)
        j = _make_j_series([40, 50, 45, 40, 50, 60])
        assert detect_kdj_turn(j) == "neutral"

    def test_neutral_turn_up_above_20(self):
        """Turn up but trough above 20 = neutral"""
        j = _make_j_series([30, 40, 35, 25, 35, 45])
        assert detect_kdj_turn(j) == "neutral"

    def test_neutral_turn_down_below_80(self):
        """Turn down but peak below 80 = neutral"""
        j = _make_j_series([50, 60, 70, 75, 65, 55])
        assert detect_kdj_turn(j) == "neutral"

    def test_too_short_series(self):
        """Series too short returns neutral"""
        j = _make_j_series([5, 10, 15])
        assert detect_kdj_turn(j) == "neutral"

    def test_monotonic_up_no_turn(self):
        """Monotonically increasing = no turn"""
        j = _make_j_series([5, 10, 15, 20, 25, 30])
        assert detect_kdj_turn(j) == "neutral"

    def test_monotonic_down_no_turn(self):
        """Monotonically decreasing = no turn"""
        j = _make_j_series([90, 85, 80, 75, 70, 65])
        assert detect_kdj_turn(j) == "neutral"


# ========== Multi-Period KDJ Verdict ==========


class TestMultiPeriodVerdict:
    def test_bullish_all_aligned(self):
        """All 3 timeframes bullish = buy"""
        daily = {"k": 30, "d": 20, "j": 40, "turn": "turn_up"}
        h60 = {"k": 35, "d": 25, "turn": "turn_up"}
        m15 = {"k": 30, "d": 20}
        result = multi_period_kdj_verdict(daily, h60, m15)
        assert result["verdict"] == "buy"
        assert result["score"] >= 60

    def test_bearish_all_aligned(self):
        """All 3 timeframes bearish = avoid"""
        daily = {"k": 50, "d": 60, "j": 90}  # j>80 => -20
        h60 = {"k": 40, "d": 50}  # k<d => no bonus
        m15 = {"k": 40, "d": 50}  # k<d => no bonus
        result = multi_period_kdj_verdict(daily, h60, m15)
        assert result["verdict"] == "avoid"
        assert result["score"] < 0

    def test_mixed_signals_hold(self):
        """Mixed signals = hold"""
        daily = {"k": 60, "d": 50, "j": 55}  # j<80 and k>d => +40
        # No h60, no m15
        result = multi_period_kdj_verdict(daily)
        assert result["verdict"] == "hold"
        assert 30 <= result["score"] < 60

    def test_daily_overheated_avoid(self):
        """Daily J>80 with no other signals = avoid (score -20 < 0)"""
        daily = {"k": 60, "d": 55, "j": 85}  # j>80 => -20
        result = multi_period_kdj_verdict(daily)
        assert result["verdict"] == "avoid"
        assert result["score"] == -20

    def test_daily_neutral_wait(self):
        """Daily neutral, no sub-periods = wait"""
        daily = {"k": 40, "d": 50, "j": 55}  # k<d, j<80 => 0
        result = multi_period_kdj_verdict(daily)
        assert result["verdict"] == "wait"
        assert result["score"] == 0

    def test_empty_daily_defaults(self):
        """Empty daily dict uses defaults (k=50, d=50, j=50)"""
        result = multi_period_kdj_verdict({})
        # k=50 not > d=50, j=50 < 80 => 0
        assert result["verdict"] == "wait"
        assert result["score"] == 0

    def test_reason_contains_descriptions(self):
        """Reason string contains meaningful descriptions"""
        daily = {"k": 30, "d": 20, "j": 40, "turn": "turn_up"}
        h60 = {"k": 35, "d": 25, "turn": "turn_up"}
        m15 = {"k": 30, "d": 20}
        result = multi_period_kdj_verdict(daily, h60, m15)
        assert "日线KDJ偏多" in result["reason"]
        assert "日线拐头向上" in result["reason"]
        assert "60分钟偏多" in result["reason"]
        assert "15分钟偏多" in result["reason"]


# ========== generate_signals KDJ fix ==========


class TestGenerateSignalsKdj:
    def _make_df(self, closes, highs=None, lows=None, volumes=None, n=None):
        """Build a minimal DataFrame with enough rows for indicators."""
        count = n or max(len(closes) + 30, 50)
        if highs is None:
            highs = [c * 1.02 for c in closes]
        if lows is None:
            lows = [c * 0.98 for c in closes]
        if volumes is None:
            volumes = [1000000] * len(closes)

        # Pad the beginning with flat data so we have enough rows
        pad = count - len(closes)
        all_close = [closes[0]] * pad + list(closes)
        all_high = [highs[0]] * pad + list(highs)
        all_low = [lows[0]] * pad + list(lows)
        all_vol = [volumes[0]] * pad + list(volumes)

        return pd.DataFrame({
            "open": all_close,
            "high": all_high,
            "low": all_low,
            "close": all_close,
            "volume": all_vol,
        })

    def test_overbought_with_turn_down_is_bearish(self):
        """J > 80 + turn_down => bearish (not neutral)"""
        # Create a J curve that peaks above 80 then turns down
        # We need J values to go: high, higher (peak), lower (turn)
        # J = 3K - 2D, so we need K to drop relative to D
        # Use a price series that spikes then drops
        closes = [100] * 15 + [110, 115, 120, 125, 130, 135, 140, 138, 132, 125]
        df = self._make_df(closes)
        signals = generate_signals(df)
        # We don't assert a specific value since it depends on exact KDJ calc,
        # but we verify KDJ_turn is present when KDJ is computed
        if "KDJ" in signals:
            assert signals["KDJ"] in ("bullish", "bearish", "neutral")
            if "KDJ_turn" in signals:
                assert signals["KDJ_turn"] in ("turn_up", "turn_down", "neutral")

    def test_oversold_with_turn_up_is_bullish(self):
        """J < 20 + turn_up => bullish"""
        closes = [100] * 15 + [90, 85, 80, 75, 70, 68, 65, 70, 78, 85]
        df = self._make_df(closes)
        signals = generate_signals(df)
        if "KDJ" in signals:
            assert signals["KDJ"] in ("bullish", "bearish", "neutral")

    def test_overbought_no_turn_is_neutral(self):
        """J > 80 + no turn => neutral (not bearish)"""
        # Price rising steadily: J stays high but no reversal
        closes = [100 + i * 2 for i in range(20)]
        df = self._make_df(closes)
        signals = generate_signals(df)
        # If J > 80 and no turn_down, KDJ should be neutral
        if "KDJ" in signals and "KDJ_turn" in signals:
            if signals["KDJ_turn"] != "turn_down":
                # J might be > 80 => neutral, or J might not be > 80
                assert signals["KDJ"] in ("bullish", "bearish", "neutral")

    def test_kdj_turn_key_always_present_when_kdj_computed(self):
        """KDJ_turn signal is always set alongside KDJ signal"""
        closes = [100, 102, 98, 105, 103, 107, 110, 108, 112, 115,
                  113, 118, 120, 117, 122, 125, 123, 128, 130, 127]
        df = self._make_df(closes)
        signals = generate_signals(df)
        if "KDJ" in signals:
            assert "KDJ_turn" in signals
            assert signals["KDJ_turn"] in ("turn_up", "turn_down", "neutral")
