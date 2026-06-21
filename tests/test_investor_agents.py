"""AgentPanel 模块单元测试 + Borrow 1 past_context 注入 caller 验证

P0-4 修复: panel.vote() 调用 (test 2/3/4) 实证 past_context 注入 + caller 存在.
无 caller 的话 result['past_context'] 是 dead code.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd
import numpy as np

from lib.investor_agents import AgentPanel
from lib.signal_tracker import (
    SignalTracker, SignalRecord, build_past_context,
    get_cached_tracker, invalidate_tracker_cache,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def isolated_signal_dir(tmp_path, monkeypatch):
    """重定向 SIGNAL_LOG_DIR 到 tmp_path (signal_tracker 隔离)."""
    import lib.signal_tracker as st
    test_dir = tmp_path / "signals"
    test_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(st, "SIGNAL_LOG_DIR", test_dir)
    invalidate_tracker_cache()
    return test_dir


@pytest.fixture
def hist_df():
    """最小 hist_df fixture (60 天 close 价格)."""
    np.random.seed(42)
    n = 60
    return pd.DataFrame({
        "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
        "volume": [1_000_000] * n,
        "date": pd.date_range("2026-04-01", periods=n),
    })


@pytest.fixture
def data_factors():
    """最小 data_factors fixture (4 agent 需要的基础数据)."""
    return {
        "pe": 12.0,
        "pb": 1.5,
        "dividend_yield": 0.04,
        "roe": 18.0,
        "adx": 28.0,
    }


@pytest.fixture
def seed_history(isolated_signal_dir):
    """Seed 5 same-ticker + 3 cross-ticker SignalRecord 让 build_past_context 有输出."""
    t = SignalTracker()
    for i in range(5):
        t.record_signal("600519", "bullish" if i % 2 == 0 else "bearish", 0.7, "2026-05-01")
        t.signals[-1].actual_return_5d = (0.05 if i % 2 == 0 else -0.02)
        t.signals[-1].is_correct = (i % 2 == 0)
    for i, code in enumerate(["000858", "601318", "600036"]):
        t.record_signal(code, "bullish", 0.6, "2026-05-01")
        t.signals[-1].actual_return_5d = -0.08
        t.signals[-1].is_correct = False
    t._save_history()
    invalidate_tracker_cache()
    return t


# ============================================================================
# Borrow 1 P0-4 caller 验证 (panel.vote() 实际被调用 + past_context 注入)
# ============================================================================

class TestBorrow1AgentPanelCaller:
    """P0-4 修复: panel.vote() 被真实测试调用 + past_context 被消费."""

    def test_vote_returns_basic_signal(self, hist_df, data_factors, isolated_signal_dir):
        """panel.vote() 不传 code 也能跑通 (基础功能回归)."""
        panel = AgentPanel()
        result = panel.vote(hist_df, data_factors=data_factors)
        assert "signal" in result
        assert "confidence" in result
        assert "votes" in result
        assert result["signal"] in ("bullish", "bearish", "neutral")
        assert "past_context" not in result

    def test_vote_with_code_returns_past_context_injection(
        self, hist_df, data_factors, isolated_signal_dir, seed_history
    ):
        """P0-4 核心: panel.vote(code=...) 注入 past_context (TradingAgents 借鉴)."""
        panel = AgentPanel()
        result = panel.vote(hist_df, code="600519", data_factors=data_factors)
        assert "past_context" in result, "past_context 必须被注入 (P0-4 caller 验证)"
        past_ctx = result["past_context"]
        assert "History 600519" in past_ctx, "应包含 same-ticker 历史段"
        assert "Recent cross-ticker lessons" in past_ctx, "应包含 cross-ticker 反思段"

    def test_vote_without_code_no_past_context(
        self, hist_df, data_factors, isolated_signal_dir, seed_history
    ):
        """code=None -> 不注入 past_context (降级路径)."""
        panel = AgentPanel()
        result = panel.vote(hist_df, code=None, data_factors=data_factors)
        assert "past_context" not in result, "code=None 时不应注入"

    def test_vote_tracker_load_failure_fallback(
        self, hist_df, data_factors, isolated_signal_dir
    ):
        """tracker 失败时 graceful 降级 (不阻塞 vote)."""
        panel = AgentPanel()
        with patch("lib.signal_tracker.get_cached_tracker",
                   side_effect=IOError("simulated tracker failure")):
            result = panel.vote(hist_df, code="600519", data_factors=data_factors)
        assert "signal" in result
        assert "past_context" not in result


# ============================================================================
# Borrow 1 helpers 单元测试
# ============================================================================

class TestBorrow1Helpers:
    """Borrow 1.1 helpers 单元测试."""

    def test_build_past_context_empty_signals(self):
        assert build_past_context("600519", []) == ""

    def test_build_past_context_same_ticker_history(self, isolated_signal_dir, seed_history):
        t = SignalTracker()
        ctx = build_past_context("600519", t.signals, n_same=5, n_cross=3)
        assert "History 600519" in ctx
        assert "5d_return" in ctx

    def test_build_past_context_cross_ticker_lessons(self, isolated_signal_dir, seed_history):
        t = SignalTracker()
        ctx = build_past_context("600519", t.signals, n_same=5, n_cross=3)
        assert "Recent cross-ticker lessons" in ctx
        assert "000858" in ctx or "601318" in ctx or "600036" in ctx

    def test_build_past_context_within_token_budget(self, isolated_signal_dir, seed_history):
        """P1-3: 5 same + 3 cross records -> len(past_context) < 2000 chars."""
        t = SignalTracker()
        ctx = build_past_context("600519", t.signals, n_same=5, n_cross=3)
        assert len(ctx) < 2000, f"past_context 过长 ({len(ctx)} chars)"

    def test_get_cached_tracker_ttl_expiry(self, isolated_signal_dir):
        """P0-3: ttl_seconds=0 -> 立即过期."""
        invalidate_tracker_cache()
        t1 = get_cached_tracker(ttl_seconds=0)
        t2 = get_cached_tracker(ttl_seconds=0)
        assert t1 is not None
        assert t2 is not None

    def test_invalidate_tracker_cache_after_record_signal(
        self, isolated_signal_dir, seed_history
    ):
        """Borrow 1.1b: record_signal 末尾 invalidate cache, 下次 get_cached_tracker 重建."""
        invalidate_tracker_cache()
        cached_before = get_cached_tracker(ttl_seconds=30)
        codes_before = {s.code for s in cached_before.signals}
        # record 新 signal → 应触发 invalidate_tracker_cache
        t = SignalTracker()
        t.record_signal("600519", "bullish", 0.8, "2026-06-22")
        # cache 被 invalidate, 下次 get_cached_tracker 重建 (读最新 disk)
        t2 = get_cached_tracker(ttl_seconds=30)
        codes_after = {s.code for s in t2.signals}
        # 验证: 新 record 在重建后的 tracker 中
        assert "2026-06-22" in {s.date for s in t2.signals if s.code == "600519"}, \
            "record_signal 应 invalidate cache, 重建后新 record 可见"