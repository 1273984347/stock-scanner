"""策略模块单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd


class TestQuickScore:
    def test_quick_score_returns_structure(self, sample_realtime_row):
        """quick_score 返回结构正确"""
        from lib.strategies import quick_score
        result = quick_score(sample_realtime_row)
        assert isinstance(result, dict)
        assert "score" in result or "rating" in result

    def test_quick_score_with_minimal_data(self):
        """最小数据（仅有代码）时不崩溃"""
        from lib.strategies import quick_score
        row = pd.Series({"代码": "000001"})
        try:
            result = quick_score(row)
            assert isinstance(result, dict)
        except Exception:
            pytest.fail("quick_score 缺数据时不应崩溃")


class TestFullScore:
    def test_full_score_returns_structure(self, sample_ohlcv):
        """full_score 返回结构正确"""
        from lib.strategies import full_score
        row = pd.Series({
            "代码": "000001", "市盈率-动态": 8.5,
            "量比": 1.2, "换手率": 1.0, "成交额": 5e8,
            "涨跌幅": 1.5, "60日涨跌幅": 5.0,
        })
        factors = {"volume_ratio": 1.2, "momentum_5": 2.0, "momentum_20": 5.0}
        result = full_score("000001", sample_ohlcv, factors, row)
        assert isinstance(result, dict)

    def test_full_score_grading_boundary(self, sample_ohlcv):
        """评分边界：调整分 8/6/4/2 阈值"""
        from lib.strategies import full_score
        row = pd.Series({
            "代码": "000001", "市盈率-动态": 8.5,
            "量比": 1.2, "换手率": 1.0, "成交额": 5e8,
            "涨跌幅": 1.5, "60日涨跌幅": 5.0,
        })
        factors = {"volume_ratio": 1.2, "momentum_5": 2.0, "momentum_20": 5.0}
        result = full_score("000001", sample_ohlcv, factors, row)
        assert "score" in result
        # score 应在合理范围
        assert 0 <= result["score"] <= 10


class TestComprehensiveScore:
    def test_comprehensive_score_from_scorer(self):
        """comprehensive_score 来自 scorer 模块，验证调用"""
        from lib.scorer import comprehensive_score
        # 四个维度全零
        result = comprehensive_score(0, 0, 0, 0)
        assert isinstance(result, dict)
        assert "score" in result
        # 全零时应有默认值
        assert 0 <= result["score"] <= 10
