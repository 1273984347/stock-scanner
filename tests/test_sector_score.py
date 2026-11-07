"""统一板块评分测试"""

import pytest


class TestSectorScore:
    def test_compute_sector_scores_returns_list(self):
        """返回列表类型"""
        from lib.strategy.sector_score import compute_sector_scores
        scores = compute_sector_scores(top_n=5)
        assert isinstance(scores, list)

    def test_score_range(self):
        """评分范围 0-100"""
        from lib.strategy.sector_score import compute_sector_scores
        scores = compute_sector_scores(top_n=5)
        for s in scores:
            assert 0 <= s.total_score <= 100
            assert 0 <= s.score_return <= 25
            assert 0 <= s.score_volume <= 25
            assert 0 <= s.score_limit_up <= 25
            assert 0 <= s.score_leader <= 25

    def test_score_dimensions_sum(self):
        """四维度之和等于总分"""
        from lib.strategy.sector_score import compute_sector_scores
        scores = compute_sector_scores(top_n=5)
        for s in scores:
            total = s.score_return + s.score_volume + s.score_limit_up + s.score_leader
            assert abs(total - s.total_score) < 0.1

    def test_get_sector_score_for_stock(self):
        """个股板块评分"""
        from lib.strategy.sector_score import get_sector_score_for_stock
        score = get_sector_score_for_stock("600519")
        assert 0 <= score <= 100

    def test_get_sector_score_unknown(self):
        """未知股票返回 50"""
        from lib.strategy.sector_score import get_sector_score_for_stock
        score = get_sector_score_for_stock("999999")
        assert score == 50.0

    def test_score_sorted_descending(self):
        """结果按总分降序排列"""
        from lib.strategy.sector_score import compute_sector_scores
        scores = compute_sector_scores(top_n=10)
        if len(scores) >= 2:
            for i in range(len(scores) - 1):
                assert scores[i].total_score >= scores[i + 1].total_score


class TestScorePureFunctions:
    """_score_* 纯函数单元测试"""

    def test_score_return_median(self):
        """中位数收益应约 12.5 分（50th 百分位 × 25）"""
        from lib.strategy.sector_score import _score_return
        all_returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        score = _score_return(0.03, all_returns)
        assert 10 <= score <= 15

    def test_score_return_best(self):
        """最高收益应约 25 分"""
        from lib.strategy.sector_score import _score_return
        all_returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        score = _score_return(0.05, all_returns)
        assert score == 25.0

    def test_score_return_worst(self):
        """最低收益应约 5 分"""
        from lib.strategy.sector_score import _score_return
        all_returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        score = _score_return(0.01, all_returns)
        assert score == 5.0

    def test_score_return_empty_list(self):
        """空列表返回 12.5（默认中位）"""
        from lib.strategy.sector_score import _score_return
        score = _score_return(0.03, [])
        assert score == 12.5

    def test_score_volume_high(self):
        """量比 2.0 应满分 25"""
        from lib.strategy.sector_score import _score_volume
        score = _score_volume(2.0)
        assert score == 25.0

    def test_score_volume_zero(self):
        """量比 0 应 0 分"""
        from lib.strategy.sector_score import _score_volume
        score = _score_volume(0.0)
        assert score == 0.0

    def test_score_volume_baseline(self):
        """量比 0.5 是公式零点，应得 0 分"""
        from lib.strategy.sector_score import _score_volume
        score = _score_volume(0.5)
        assert score == 0.0

    def test_score_limit_up_proportional(self):
        """涨停占比线性评分"""
        from lib.strategy.sector_score import _score_limit_up
        score = _score_limit_up(5, 100)
        assert score == 12.5

    def test_score_limit_up_capped(self):
        """涨停评分上限 25"""
        from lib.strategy.sector_score import _score_limit_up
        score = _score_limit_up(20, 50)
        assert score == 25.0

    def test_score_limit_up_zero_block(self):
        """板块大小为 0 返回 0"""
        from lib.strategy.sector_score import _score_limit_up
        score = _score_limit_up(5, 0)
        assert score == 0

    def test_score_leader_percentile(self):
        """龙头强度按百分位评分"""
        from lib.strategy.sector_score import _score_leader
        all_returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        score = _score_leader(0.05, all_returns)
        assert score == 25.0

    def test_score_leader_empty_list(self):
        """龙头强度空列表返回 12.5"""
        from lib.strategy.sector_score import _score_leader
        score = _score_leader(0.03, [])
        assert score == 12.5
