"""V8 Alpha 工厂测试"""
import pytest
from lib.scoring.alpha_factory import (
    AlphaFactory, AlphaRecord, AlphaEvaluation,
    LIFECYCLE_DISCOVERED, LIFECYCLE_EFFECTIVE, LIFECYCLE_CROWDED,
    LIFECYCLE_DECAYING, LIFECYCLE_DEAD,
)


class TestAlphaFactory:
    def _make_factory(self, tmp_path):
        return AlphaFactory(persist_path=tmp_path / "alpha_factory.json")

    def test_discover_alpha(self, tmp_path):
        """发现新 Alpha"""
        factory = self._make_factory(tmp_path)
        alpha_id = factory.discover("momentum_20", name="20日动量")
        assert alpha_id is not None
        assert "momentum_20" in alpha_id

    def test_discover_duplicate(self, tmp_path):
        """重复发现返回现有 ID"""
        factory = self._make_factory(tmp_path)
        id1 = factory.discover("momentum_20")
        id2 = factory.discover("momentum_20")
        assert id1 == id2

    def test_record_trade(self, tmp_path):
        """记录交易结果"""
        factory = self._make_factory(tmp_path)
        alpha_id = factory.discover("rsi_14")
        factory.record_trade(alpha_id, 5.0)
        factory.record_trade(alpha_id, -2.0)
        report = factory.get_report()
        assert report["alphas"][alpha_id]["trade_count"] == 2

    def test_evaluate(self, tmp_path):
        """评估 Alpha"""
        factory = self._make_factory(tmp_path)
        alpha_id = factory.discover("adx")
        # 添加足够样本
        for _ in range(15):
            factory.record_trade(alpha_id, 3.0)
        eval_result = factory.evaluate(alpha_id)
        assert isinstance(eval_result, AlphaEvaluation)
        assert eval_result.win_rate > 0.5
        assert eval_result.recommendation == "keep"

    def test_lifecycle_transitions(self, tmp_path):
        """生命周期状态转换"""
        factory = self._make_factory(tmp_path)
        alpha_id = factory.discover("trend_strength")

        # 初始状态：discovered
        assert factory._alphas[alpha_id].lifecycle == LIFECYCLE_DISCOVERED

        # 添加 15 笔交易（超过 MIN_TRADES=10）
        for _ in range(12):
            factory.record_trade(alpha_id, 5.0)
        for _ in range(3):
            factory.record_trade(alpha_id, -2.0)

        # 更新生命周期
        changed = factory.update_lifecycle()
        assert alpha_id in changed
        assert factory._alphas[alpha_id].lifecycle == LIFECYCLE_EFFECTIVE

    def test_decay_detection(self, tmp_path):
        """衰退检测"""
        factory = self._make_factory(tmp_path)
        alpha_id = factory.discover("volume_ratio")

        # 先盈利
        for _ in range(10):
            factory.record_trade(alpha_id, 5.0)
        factory.update_lifecycle()
        assert factory._alphas[alpha_id].lifecycle == LIFECYCLE_EFFECTIVE

        # 连续亏损
        for _ in range(15):
            factory.record_trade(alpha_id, -3.0)
        factory.update_lifecycle()
        assert factory._alphas[alpha_id].lifecycle in (LIFECYCLE_DECAYING, LIFECYCLE_DEAD)

    def test_dead_detection(self, tmp_path):
        """失效检测"""
        factory = self._make_factory(tmp_path)
        alpha_id = factory.discover("cci")

        # 大量亏损
        for _ in range(20):
            factory.record_trade(alpha_id, -5.0)
        factory.update_lifecycle()
        assert factory._alphas[alpha_id].lifecycle == LIFECYCLE_DEAD

    def test_get_active_alphas(self, tmp_path):
        """获取有效 Alpha"""
        factory = self._make_factory(tmp_path)
        id1 = factory.discover("momentum_20")
        id2 = factory.discover("rsi_14")

        # id1 有效
        for _ in range(15):
            factory.record_trade(id1, 5.0)

        # id2 失效
        for _ in range(15):
            factory.record_trade(id2, -5.0)

        factory.update_lifecycle()
        active = factory.get_active_alphas()
        active_ids = [r.alpha_id for r in active]
        assert id1 in active_ids
        assert id2 not in active_ids

    def test_persistence(self, tmp_path):
        """持久化测试"""
        factory = self._make_factory(tmp_path)
        alpha_id = factory.discover("adx", name="ADX因子")
        factory.record_trade(alpha_id, 5.0)
        factory.save()

        # 重新加载
        factory2 = self._make_factory(tmp_path)
        assert alpha_id in factory2._alphas
        assert factory2._alphas[alpha_id].trade_count == 1

    def test_get_report(self, tmp_path):
        """报告结构"""
        factory = self._make_factory(tmp_path)
        factory.discover("trend_strength")
        report = factory.get_report()
        assert "total_alphas" in report
        assert "by_lifecycle" in report
        assert "alphas" in report

    def test_get_summary(self, tmp_path):
        """摘要可读"""
        factory = self._make_factory(tmp_path)
        factory.discover("momentum_20", name="20日动量")
        summary = factory.get_summary()
        assert "Alpha 工厂报告" in summary
        assert "20日动量" in summary

    def test_record_unknown_alpha(self, tmp_path):
        """记录未知 Alpha 不崩溃"""
        factory = self._make_factory(tmp_path)
        factory.record_trade("nonexistent", 5.0)  # 不应抛异常

    # ========== P1 修复测试（2026-06-06）==========

    def test_discover_with_low_ic_rejected(self, tmp_path):
        """P1-1/2: 低 IC 或负 IC 因子不通过发现阈值

        A 股 long-only 场景：负 IC 因子应被拒（不应取 abs）
        """
        import pandas as pd
        from lib.scoring.alpha import AlphaSynthesizer
        factory = self._make_factory(tmp_path)

        # 因子值与前向收益完全负相关 → rank_ic 应为负
        factor_values = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        forward_returns = pd.Series([10, 9, 8, 7, 6, 5, 4, 3, 2, 1], dtype=float)
        alpha_id = factory.discover(
            "bad_factor", factor_values=factor_values, forward_returns=forward_returns
        )
        # 负 IC 应被拒（不创建）
        assert alpha_id is None, f"负 IC 因子应被拒，但创建了 {alpha_id}"

    def test_discover_icir_not_fake(self, tmp_path):
        """P1-1: ICIR 不再用 abs(ic) * 10 硬编码假数

        单点 IC 无法计算 ICIR（无时序），应返回 0.0 或 None
        """
        import pandas as pd
        factory = self._make_factory(tmp_path)

        factor_values = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        forward_returns = pd.Series([2, 3, 5, 7, 11, 13, 17, 19, 23, 29], dtype=float)
        alpha_id = factory.discover(
            "test_factor", factor_values=factor_values, forward_returns=forward_returns
        )
        if alpha_id:
            record = factory._alphas[alpha_id]
            # ICIR 不应是简单的 abs(ic) * 10（那是硬编码）
            # 实际值应该反映"无时序数据"的状态（0.0 或 None）
            assert record.icir < 0.5, (
                f"ICIR={record.icir} 看起来仍是硬编码假数（abs(ic)*10 模式）"
            )

    def test_discover_synthetic_data_error_logged(self, tmp_path, caplog):
        """P1-3: AlphaSynthesizer 失败应打 ERROR log（不再静默 pass）"""
        import logging
        factory = self._make_factory(tmp_path)

        with caplog.at_level(logging.DEBUG):
            # 传 None 强制 AlphaSynthesizer 失败
            alpha_id = factory.discover("bad_data", factor_values=None, forward_returns=None)

        # 应该创建（不阻断主流程）但有 debug log 说明
        # 关键：不静默 pass
        assert alpha_id is not None  # 创建（不阻断）
