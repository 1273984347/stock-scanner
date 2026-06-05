"""Alpha 反馈闭环测试"""
import pytest
from pathlib import Path
from lib.alpha_feedback import AlphaFeedback, AlphaStats


class TestAlphaStats:
    """AlphaStats 数据结构测试"""

    def test_win_rate(self):
        stats = AlphaStats(alpha_type="trend", total_trades=10, wins=6, losses=4)
        assert stats.win_rate == 0.6

    def test_win_rate_zero_trades(self):
        stats = AlphaStats(alpha_type="trend")
        assert stats.win_rate == 0.0

    def test_avg_pnl(self):
        stats = AlphaStats(
            alpha_type="trend", total_trades=3, wins=2, losses=1,
            total_pnl=8.0, pnl_history=[5.0, 3.0, -2.0],
        )
        assert abs(stats.avg_pnl - 8.0 / 3) < 0.01

    def test_profit_factor(self):
        stats = AlphaStats(
            alpha_type="trend", pnl_history=[5.0, -2.0, 3.0, -1.0],
        )
        # gross_win = 8, gross_loss = 3
        assert abs(stats.profit_factor - 8.0 / 3.0) < 0.01

    def test_sharpe(self):
        stats = AlphaStats(
            alpha_type="trend", pnl_history=[5.0, -2.0, 3.0, -1.0, 4.0],
        )
        assert stats.sharpe > 0  # 正期望


class TestAlphaFeedback:
    """AlphaFeedback 测试"""

    def _make_fb(self, tmp_path):
        return AlphaFeedback(persist_path=tmp_path / "alpha_feedback.json")

    def test_record_trade(self, tmp_path):
        fb = self._make_fb(tmp_path)
        fb.record_trade("trend", 5.2, "TREND_UP", "AI")
        report = fb.get_alpha_report()
        assert "trend" in report
        assert report["trend"]["total_trades"] == 1
        assert report["trend"]["wins"] == 1

    def test_record_loss(self, tmp_path):
        fb = self._make_fb(tmp_path)
        fb.record_trade("trend", -2.1, "TREND_UP", "AI")
        report = fb.get_alpha_report()
        assert report["trend"]["losses"] == 1
        assert report["trend"]["wins"] == 0

    def test_multiple_alpha_types(self, tmp_path):
        fb = self._make_fb(tmp_path)
        fb.record_trade("trend", 5.0, "TREND_UP", "AI")
        fb.record_trade("sector_rotation", 3.0, "TREND_UP", "半导体")
        fb.record_trade("mean_reversion", -1.0, "MEAN_REVERSION", "消费")
        report = fb.get_alpha_report()
        assert len(report) == 3

    def test_get_alpha_weights(self, tmp_path):
        fb = self._make_fb(tmp_path)
        # trend: 3胜1负 = 75%胜率
        fb.record_trade("trend", 5.0, "TREND_UP", "AI")
        fb.record_trade("trend", 3.0, "TREND_UP", "AI")
        fb.record_trade("trend", 2.0, "TREND_UP", "AI")
        fb.record_trade("trend", -1.0, "TREND_UP", "AI")
        # sentiment: 1胜3负 = 25%胜率
        fb.record_trade("sentiment", 2.0, "TREND_UP", "AI")
        fb.record_trade("sentiment", -3.0, "TREND_UP", "AI")
        fb.record_trade("sentiment", -2.0, "TREND_UP", "AI")
        fb.record_trade("sentiment", -1.0, "TREND_UP", "AI")

        weights = fb.get_alpha_weights()
        # trend 应该有权重，sentiment 应该被淘汰（胜率<30%）
        assert weights.get("trend", 0) > 0
        assert weights.get("sentiment", 0) == 0.0

    def test_weights_sum_to_one(self, tmp_path):
        fb = self._make_fb(tmp_path)
        fb.record_trade("trend", 5.0, "TREND_UP", "AI")
        fb.record_trade("sector_rotation", 3.0, "TREND_UP", "半导体")
        fb.record_trade("mean_reversion", -1.0, "MEAN_REVERSION", "消费")
        weights = fb.get_alpha_weights()
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01

    def test_detect_alpha_decay(self, tmp_path):
        fb = self._make_fb(tmp_path)
        # 20笔交易，最近20笔全亏
        for _ in range(5):
            fb.record_trade("trend", 5.0, "TREND_UP", "AI")
        for _ in range(20):
            fb.record_trade("trend", -2.0, "TREND_UP", "AI")
        decayed = fb.detect_alpha_decay(lookback=20)
        assert "trend" in decayed

    def test_persistence(self, tmp_path):
        fb = self._make_fb(tmp_path)
        fb.record_trade("trend", 5.0, "TREND_UP", "AI")
        fb.record_trade("trend", -2.0, "TREND_UP", "AI")
        fb.save()

        # 重新加载
        fb2 = self._make_fb(tmp_path)
        report = fb2.get_alpha_report()
        assert report["trend"]["total_trades"] == 2

    def test_get_summary(self, tmp_path):
        fb = self._make_fb(tmp_path)
        fb.record_trade("trend", 5.0, "TREND_UP", "AI")
        summary = fb.get_summary()
        assert "Alpha 反馈报告" in summary
        assert "trend" in summary

    def test_empty_feedback(self, tmp_path):
        fb = self._make_fb(tmp_path)
        assert fb.get_alpha_weights() == {}
        assert fb.get_alpha_report() == {}
        assert fb.detect_alpha_decay() == []
