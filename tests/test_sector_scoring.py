"""板块评分引擎测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest

from lib.strategies import _compute_sector_score, _is_sector_declining


class TestSectorScoring:
    """四维板块评分测试"""

    def _make_sector_df(self, **overrides):
        """构造板块聚合数据"""
        data = {
            "所属行业": ["半导体", "白酒", "医药"],
            "涨停数": [10, 5, 2],
            "最高连板": [4, 2, 1],
            "代表股": ["A、B、C", "D、E", "F"],
            "平均封板资金": [1e8, 5e7, 2e7],
        }
        data.update(overrides)
        return pd.DataFrame(data)

    def test_scoring_dimensions(self):
        """四维评分：所有维度都参与计算"""
        df = self._make_sector_df()
        result = _compute_sector_score(df)

        assert "zt_score" in result.columns
        assert "ret_score" in result.columns
        assert "vol_score" in result.columns
        assert "leader_score" in result.columns
        assert "sector_score" in result.columns

    def test_score_range(self):
        """总分在 0-100 范围内"""
        df = self._make_sector_df()
        result = _compute_sector_score(df)

        assert result["sector_score"].between(0, 100).all()

    def test_score_ranking(self):
        """涨停数最高 + 连板最高的板块排名第一"""
        df = self._make_sector_df()
        result = _compute_sector_score(df)

        # 半导体涨停数=10, 连板=4, 应排第一
        assert result.iloc[0]["所属行业"] == "半导体"

    def test_missing_dimensions_fallback(self):
        """缺失涨幅/成交额维度时降级到默认分值"""
        df = self._make_sector_df()
        # 不含 '5日涨幅' 和 '成交额变化'
        result = _compute_sector_score(df)

        # 降级维度应取默认值 12.5
        assert (result["ret_score"] == 12.5).all()
        assert (result["vol_score"] == 12.5).all()

    def test_with_all_dimensions(self):
        """完整四维数据时各维度独立评分"""
        df = self._make_sector_df(**{
            "5日涨幅": [8.5, 3.2, -1.0],
            "成交额变化": [100, 60, 30],
        })
        result = _compute_sector_score(df)

        # 涨幅最高的半导体应该 ret_score 最高
        semi = result[result["所属行业"] == "半导体"].iloc[0]
        pharma = result[result["所属行业"] == "医药"].iloc[0]
        assert semi["ret_score"] > pharma["ret_score"]

    def test_single_sector(self):
        """单板块时 rank(pct=True) 返回 1.0"""
        df = pd.DataFrame({
            "所属行业": ["半导体"],
            "涨停数": [10],
            "最高连板": [3],
        })
        result = _compute_sector_score(df)
        assert result.iloc[0]["zt_score"] == 25.0
        assert result.iloc[0]["leader_score"] == 25.0


class TestSectorDeclineDetection:
    """板块退潮检测测试"""

    def test_decline_when_few_limit_ups(self):
        """涨停数合计 < 3 触发退潮信号"""
        df = pd.DataFrame({
            "涨停数": [1, 1],
            "最高连板": [1, 1],
        })
        assert _is_sector_declining(df) is True

    def test_not_declining(self):
        """涨停数充足时不触发退潮"""
        df = pd.DataFrame({
            "涨停数": [5, 3],
            "最高连板": [3, 2],
        })
        assert _is_sector_declining(df) is False

    def test_empty_df_is_declining(self):
        """空 DataFrame 视为退潮"""
        df = pd.DataFrame({"涨停数": [], "最高连板": []})
        assert _is_sector_declining(df) is True

    def test_boundary_exactly_3(self):
        """涨停数恰好等于 3 不视为退潮"""
        df = pd.DataFrame({
            "涨停数": [2, 1],
            "最高连板": [2, 1],
        })
        assert _is_sector_declining(df) is False
