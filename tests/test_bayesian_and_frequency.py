"""贝叶斯更新器 + 策略降频机制测试"""
import pytest
import json
import tempfile
from pathlib import Path

from lib.scoring.bayesian_updater import BayesianUpdater, ConditionStats
from lib.strategy.pipeline import StrategyFailureTracker


# ========== 贝叶斯更新器 ==========

class TestConditionStats:
    def test_initial_state(self):
        stats = ConditionStats()
        assert stats.total == 0
        assert stats.win_rate == 0.5  # 先验

    def test_update_win(self):
        stats = ConditionStats()
        stats.update(True)
        assert stats.wins == 1
        assert stats.losses == 0
        assert stats.win_rate == 1.0

    def test_update_loss(self):
        stats = ConditionStats()
        stats.update(False)
        assert stats.wins == 0
        assert stats.losses == 1
        assert stats.win_rate == 0.0


class TestBayesianUpdater:
    def _make_updater(self, tmp_path):
        state_path = tmp_path / "bayesian_state.json"
        return BayesianUpdater(state_path=state_path)

    def test_no_data_returns_prior(self, tmp_path):
        updater = self._make_updater(tmp_path)
        assert updater.get_posterior("TREND_UP", "high", "breakout") == 0.5

    def test_update_and_get_posterior(self, tmp_path):
        updater = self._make_updater(tmp_path)
        # 5胜0负
        for _ in range(5):
            updater.update("TREND_UP", "high", "breakout", True)
        posterior = updater.get_posterior("TREND_UP", "high", "breakout")
        # 贝叶斯平滑（min_samples=5）：(5 + 0.5*5) / (5 + 5) = 7.5/10 = 0.75
        assert 0.73 < posterior < 0.77

    def test_bayesian_smoothing_with_few_samples(self, tmp_path):
        updater = self._make_updater(tmp_path)
        # 只有1胜0负，应向先验收缩
        updater.update("TREND_UP", "high", "breakout", True)
        posterior = updater.get_posterior("TREND_UP", "high", "breakout")
        # (1 + 5) / (1 + 10) = 6/11 ≈ 0.545
        assert 0.50 < posterior < 0.60

    def test_many_samples_converges(self, tmp_path):
        updater = self._make_updater(tmp_path)
        # 80胜20负
        for _ in range(80):
            updater.update("TREND_UP", "high", "breakout", True)
        for _ in range(20):
            updater.update("TREND_UP", "high", "breakout", False)
        posterior = updater.get_posterior("TREND_UP", "high", "breakout")
        # (80 + 5) / (100 + 10) = 85/110 ≈ 0.773
        assert 0.75 < posterior < 0.80

    def test_different_conditions_independent(self, tmp_path):
        updater = self._make_updater(tmp_path)
        for _ in range(10):
            updater.update("TREND_UP", "high", "breakout", True)
        for _ in range(10):
            updater.update("TREND_DOWN", "low", "breakout", False)
        assert updater.get_posterior("TREND_UP", "high", "breakout") > 0.6
        assert updater.get_posterior("TREND_DOWN", "low", "breakout") < 0.4

    def test_weight_adjustment(self, tmp_path):
        updater = self._make_updater(tmp_path)
        # 无数据时权重不变
        assert updater.get_weight_adjustment("TREND_UP", "high", "x") == 1.0

        # 高胜率 → 权重上调
        for _ in range(30):
            updater.update("TREND_UP", "high", "breakout", True)
        w = updater.get_weight_adjustment("TREND_UP", "high", "breakout")
        assert w > 1.0

    def test_persistence(self, tmp_path):
        updater = self._make_updater(tmp_path)
        for _ in range(5):
            updater.update("TREND_UP", "high", "breakout", True)
        updater.save()

        # 重新加载
        updater2 = self._make_updater(tmp_path)
        posterior = updater2.get_posterior("TREND_UP", "high", "breakout")
        assert posterior > 0.5

    def test_stats_summary(self, tmp_path):
        updater = self._make_updater(tmp_path)
        for _ in range(5):
            updater.update("TREND_UP", "high", "breakout", True)
        for _ in range(2):
            updater.update("TREND_UP", "high", "breakout", False)
        summary = updater.get_stats_summary()
        assert "TREND_UP|high|breakout" in summary
        assert summary["TREND_UP|high|breakout"]["total"] == 7


# ========== 策略降频机制 ==========

class TestStrategyFailureTracker:
    def _make_tracker(self, tmp_path):
        state_path = tmp_path / "strategy_failures.json"
        tracker = StrategyFailureTracker()
        tracker._path = state_path
        tracker._failures = {}
        return tracker

    def test_initial_state(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        assert not tracker.should_skip("任何策略")
        assert tracker.get_failure_count("任何策略") == 0

    def test_failure_accumulates(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_failure("涨停板")
        assert tracker.get_failure_count("涨停板") == 1
        assert not tracker.should_skip("涨停板")

    def test_skip_after_threshold(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        for _ in range(3):
            tracker.record_failure("涨停板")
        assert tracker.should_skip("涨停板")
        assert tracker.get_failure_count("涨停板") == 3

    def test_success_resets_counter(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_failure("涨停板")
        tracker.record_failure("涨停板")
        tracker.record_success("涨停板")
        assert tracker.get_failure_count("涨停板") == 0
        assert not tracker.should_skip("涨停板")

    def test_independent_strategies(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        for _ in range(3):
            tracker.record_failure("涨停板")
        assert tracker.should_skip("涨停板")
        assert not tracker.should_skip("量价突破")

    def test_persistence(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.record_failure("涨停板")
        tracker.record_failure("涨停板")
        tracker.save()

        # 重新加载
        tracker2 = StrategyFailureTracker()
        tracker2._path = tracker._path
        tracker2._load()
        assert tracker2.get_failure_count("涨停板") == 2
