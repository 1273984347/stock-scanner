"""P0-1: score_news 数据缺失默认行为

问题: score_news 默认 no_negative=True，新闻数据缺失时虚高 +2 分。
修复: 改为默认 False（缺数据=中性），调用方需显式传 True 才加分。
"""
import logging

import pytest

from lib.scoring.scorer import score_news


class TestScoreNewsDefaultBehavior:
    """P0-1: score_news 不应在数据缺失时虚高 +2 分"""

    def test_default_no_data_gives_zero(self):
        """P0-1 红: 调用方无任何新闻数据时（全部默认），应得 0 分（中性）"""
        # 不传任何参数 = 模拟"没有数据"
        score = score_news()
        assert score == 0.0, (
            f"数据缺失时默认应得 0 分（中性），"
            f"实际得 {score}（说明 no_negative 默认值仍是 True，虚高 +2）"
        )

    def test_default_does_not_inflate_score(self):
        """P0-1: 默认不应虚高 —— 当其他信号也无时总分=0"""
        # 模拟 paper trading 入场评估：仅传部分信号
        score_only_no_negative = score_news()  # 数据缺失
        assert score_only_no_negative == 0.0

    def test_explicit_no_negative_true_still_gives_two(self):
        """P0-1: 显式传 no_negative=True 仍应得 +2（保留旧能力）"""
        score = score_news(no_negative=True)
        assert score == 2.0

    def test_explicit_no_negative_false_gives_zero(self):
        """P0-1: 显式传 no_negative=False 得 0"""
        assert score_news(no_negative=False) == 0.0

    def test_other_signals_still_work(self):
        """P0-1: 修复不应影响其他信号"""
        # 不传 no_negative，但显式传其他正面信号
        score = score_news(
            major_positive=True,
            institutional_buy=True,
        )
        assert score == 5.0  # 3 + 2

    def test_data_missing_logs_warning(self):
        """P0-1: 数据缺失时应记录告警日志，方便排查污染源"""
        # lib logger 有 propagate=0（tests/logging.conf），
        # 直接挂 handler 收集记录。
        scorer_logger = logging.getLogger("lib.scoring.scorer")
        records = []

        class _CollectHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _CollectHandler(level=logging.WARNING)
        scorer_logger.addHandler(h)
        try:
            score_news()  # 全默认 = 数据缺失
        finally:
            scorer_logger.removeHandler(h)

        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert len(warnings) >= 1, "数据缺失时应打 WARNING 日志"
        msg = " ".join(r.getMessage() for r in warnings)
        assert "no_negative" in msg or "news" in msg.lower()


class TestScoreNewsIntegrationWithComprehensive:
    """P0-1: 综合评分不应因新闻缺失而虚高"""

    def test_comprehensive_score_no_inflation_without_data(self):
        """P0-1: 四维评分里 news 维度不应再贡献 +2

        修复前：news=2（虚高） → 综合分被推高
        修复后：news=0（中性） → 综合分仅由其他维度贡献
        """
        from lib.scoring.scorer import (
            comprehensive_score,
            score_fundamental,
            score_capital_flow,
            score_technical,
        )
        # news 默认（数据缺失），其他维度都给 0
        result_default = comprehensive_score(
            fundamental=score_fundamental(),
            capital_flow=score_capital_flow(),
            news=score_news(),  # 数据缺失 → news=0
            technical=score_technical(),
        )
        # 关键断言：news 维度是 0（中性），不应是 2
        assert result_default["breakdown"]["news"] == 0.0, (
            f"新闻缺失时 news 维度应为 0，实际: {result_default['breakdown']['news']}"
        )

        # 对照组：显式 no_negative=True 时 news=2
        result_explicit = comprehensive_score(
            fundamental=score_fundamental(),
            capital_flow=score_capital_flow(),
            news=score_news(no_negative=True),
            technical=score_technical(),
        )
        assert result_explicit["breakdown"]["news"] == 2.0

        # 关键：默认 < 显式
        assert result_default["score"] < result_explicit["score"], (
            f"修复后默认综合分应低于显式 no_negative=True 的情形，"
            f"默认: {result_default['score']}, 显式: {result_explicit['score']}"
        )
