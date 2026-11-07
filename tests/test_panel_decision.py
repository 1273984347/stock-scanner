"""panel_decision 模块单元测试 (Borrow 1 production caller 验证)

P0-4 修复: get_panel_decision() 在 production 路径调用 panel.vote() + past_context 注入.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch

from lib.scoring.panel_decision import get_panel_decision, format_panel_decision
from lib.scoring.signal_tracker import SignalTracker, invalidate_tracker_cache


@pytest.fixture
def isolated_signal_dir(tmp_path, monkeypatch):
    import lib.scoring.signal_tracker as st
    test_dir = tmp_path / "signals"
    test_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(st, "SIGNAL_LOG_DIR", test_dir)
    invalidate_tracker_cache()
    return test_dir


@pytest.fixture
def hist_df():
    np.random.seed(42)
    n = 60
    return pd.DataFrame({
        "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
        "volume": [1_000_000] * n,
        "date": pd.date_range("2026-04-01", periods=n),
    })


@pytest.fixture
def data_factors():
    return {"pe": 12.0, "pb": 1.5, "dividend_yield": 0.04, "roe": 18.0, "adx": 28.0}


@pytest.fixture
def seed_history(isolated_signal_dir):
    t = SignalTracker()
    for i in range(3):
        t.record_signal("600519", "bullish" if i % 2 == 0 else "bearish", 0.7, "2026-05-01")
        t.signals[-1].actual_return_5d = 0.05 if i % 2 == 0 else -0.02
        t.signals[-1].is_correct = (i % 2 == 0)
    t.record_signal("000858", "bullish", 0.6, "2026-05-01")
    t.signals[-1].actual_return_5d = -0.08
    t.signals[-1].is_correct = False
    t._save_history()
    invalidate_tracker_cache()
    return t


class TestPanelDecision:
    """get_panel_decision + format_panel_decision 测试."""

    def test_get_panel_decision_returns_signal(
        self, hist_df, data_factors, isolated_signal_dir
    ):
        """get_panel_decision 返回基础信号."""
        result = get_panel_decision("600519", hist_df, data_factors=data_factors)
        assert "signal" in result
        assert "confidence" in result
        assert result["signal"] in ("bullish", "bearish", "neutral")

    def test_get_panel_decision_injects_past_context(
        self, hist_df, data_factors, isolated_signal_dir, seed_history
    ):
        """P0-4: get_panel_decision 调用 panel.vote() + past_context 注入."""
        result = get_panel_decision("600519", hist_df, data_factors=data_factors)
        assert "past_context" in result, "past_context 必须被注入 (production caller 实证)"
        assert "History 600519" in result["past_context"]

    def test_get_panel_decision_no_code_no_past_context(
        self, hist_df, data_factors, isolated_signal_dir, seed_history
    ):
        """code=None -> 不注入 past_context."""
        result = get_panel_decision(None, hist_df, data_factors=data_factors)
        assert "past_context" not in result

    def test_format_panel_decision_basic(self, hist_df, data_factors, isolated_signal_dir):
        """format_panel_decision 输出可读文本."""
        result = get_panel_decision("600519", hist_df, data_factors=data_factors)
        text = format_panel_decision(result, "600519")
        assert "600519" in text
        assert "信号" in text
        assert "置信度" in text

    def test_format_panel_decision_with_past_context(
        self, hist_df, data_factors, isolated_signal_dir, seed_history
    ):
        """format_panel_decision 含 past_context 时输出历史反思段."""
        result = get_panel_decision("600519", hist_df, data_factors=data_factors)
        text = format_panel_decision(result, "600519")
        if "past_context" in result:
            assert "历史反思" in text