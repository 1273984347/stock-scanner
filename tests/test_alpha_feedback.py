"""Alpha 反馈闭环测试"""
import pytest
from pathlib import Path
from lib.scoring.alpha_feedback import AlphaFeedback, AlphaStats


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
        # trend: 3胜1负 = 75% 胜率
        fb.record_trade("trend", 5.0, "TREND_UP", "AI")
        fb.record_trade("trend", 3.0, "TREND_UP", "AI")
        fb.record_trade("trend", 2.0, "TREND_UP", "AI")
        fb.record_trade("trend", -1.0, "TREND_UP", "AI")
        # sentiment: 1胜9负 = 10% 胜率（Beta 平滑后 posterior = 2/12 = 0.167，触发 kill switch）
        for _ in range(1):
            fb.record_trade("sentiment", 2.0, "TREND_UP", "AI")
        for _ in range(9):
            fb.record_trade("sentiment", -2.0, "TREND_UP", "AI")

        weights = fb.get_alpha_weights()
        # trend 应该有权重，sentiment 应该被淘汰（posterior<30%）
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

    # ========== P1-4 修复测试（2026-06-06）：真 Beta-Binomial 贝叶斯 ==========

    def test_bayesian_posterior_with_zero_evidence(self, tmp_path):
        """P1-4: 0 笔样本时，权重应 = 先验（0.5），不是任意的 0.5 占位

        Beta(1,1) prior → posterior mean = 1/(1+1) = 0.5
        """
        fb = self._make_fb(tmp_path)
        # 1 笔 trend（posterior = 2/3 ≈ 0.667）+ 1 笔 baseline（posterior = 0.5）
        fb.record_trade("trend", 5.0)
        fb.record_trade("baseline", 0.0)  # 平局（不计入 wins/losses）
        weights = fb.get_alpha_weights()
        # trend posterior = (1+1)/(2+1) = 0.667
        # baseline posterior = (1+0)/(2+1) = 0.333
        # trend 占 2/3，baseline 占 1/3
        assert 0.6 < weights.get("trend", 0) < 0.75, (
            f"trend posterior=0.667，归一化后应 ~0.667，实际 {weights.get('trend')}"
        )

    def test_bayesian_smoothing_small_sample(self, tmp_path):
        """P1-4: 小样本平滑（4 笔 vs 30 笔）

        - trend: 4 笔（3 胜 1 负）→ posterior = 4/6 ≈ 0.667
        - baseline: 30 笔（20 胜 10 负）→ posterior = 21/32 ≈ 0.656
        - 朴素 win_rate 算：trend 0.75 vs baseline 0.667（差 8.3%）
        - Beta 平滑：trend 0.667 vs baseline 0.656（差 1.1%）
        - 小样本不应过拟合
        """
        fb = self._make_fb(tmp_path)
        for _ in range(3):
            fb.record_trade("trend", 5.0)
        fb.record_trade("trend", -1.0)
        for _ in range(20):
            fb.record_trade("baseline", 5.0)
        for _ in range(10):
            fb.record_trade("baseline", -1.0)
        weights = fb.get_alpha_weights()
        # 平滑后两个 posterior 接近（0.667 vs 0.656），差 ~1%
        # 朴素算则差 8.3%
        trend_w = weights.get("trend", 0)
        baseline_w = weights.get("baseline", 0)
        diff = abs(trend_w - baseline_w)
        assert diff < 0.05, (
            f"小样本平滑后两 weight 应接近（差<5%），实际 trend={trend_w}, baseline={baseline_w}, diff={diff}"
        )

    def test_bayesian_smoothing_large_sample(self, tmp_path):
        """P1-4: 大样本下平滑影响变小

        30 笔：20 胜 10 负
        - 朴素 = 0.667
        - Beta = (1+20) / (2+30) = 21/32 = 0.656
        - 大样本下两者差异小
        """
        fb = self._make_fb(tmp_path)
        for _ in range(20):
            fb.record_trade("trend", 5.0)
        for _ in range(10):
            fb.record_trade("trend", -1.0)
        # baseline: 1 笔 win（posterior = (1+1)/(2+1) = 0.667）
        fb.record_trade("baseline", 5.0)
        weights = fb.get_alpha_weights()
        # trend posterior 0.656, baseline 0.667
        # 两者几乎相等，weight 各 ~50%
        trend_w = weights.get("trend", 0)
        baseline_w = weights.get("baseline", 0)
        assert abs(trend_w - baseline_w) < 0.05, (
            f"两者 posterior 相近（差<5%），实际 trend={trend_w}, baseline={baseline_w}"
        )

    def test_bayesian_kill_switch_below_30pct(self, tmp_path):
        """P1-4: posterior < 0.30 触发 kill switch（不再粗用 win_rate < 0.30）"""
        fb = self._make_fb(tmp_path)
        # trend: 5 胜 95 负 → posterior = 6/102 ≈ 0.059
        for _ in range(5):
            fb.record_trade("trend", 1.0)
        for _ in range(95):
            fb.record_trade("trend", -1.0)
        # baseline: 5 胜 5 负 → posterior = 6/12 = 0.5（正常）
        for _ in range(5):
            fb.record_trade("baseline", 1.0)
        for _ in range(5):
            fb.record_trade("baseline", -1.0)
        weights = fb.get_alpha_weights()
        # trend posterior < 0.30 → kill switch → 不在 weights
        # baseline 占全部
        assert "trend" not in weights or weights.get("trend") == 0.0
        assert weights.get("baseline", 0) > 0.9  # 几乎占全部
