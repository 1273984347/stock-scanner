"""统一板块评分测试"""

import pytest


class TestSectorScore:
    def test_compute_sector_scores_returns_list(self):
        """返回列表类型"""
        from lib.sector_score import compute_sector_scores
        scores = compute_sector_scores(top_n=5)
        assert isinstance(scores, list)

    def test_score_range(self):
        """评分范围 0-100"""
        from lib.sector_score import compute_sector_scores
        scores = compute_sector_scores(top_n=5)
        for s in scores:
            assert 0 <= s.total_score <= 100
            assert 0 <= s.score_return <= 25
            assert 0 <= s.score_volume <= 25
            assert 0 <= s.score_limit_up <= 25
            assert 0 <= s.score_leader <= 25

    def test_score_dimensions_sum(self):
        """四维度之和等于总分"""
        from lib.sector_score import compute_sector_scores
        scores = compute_sector_scores(top_n=5)
        for s in scores:
            total = s.score_return + s.score_volume + s.score_limit_up + s.score_leader
            assert abs(total - s.total_score) < 0.1

    def test_get_sector_score_for_stock(self):
        """个股板块评分"""
        from lib.sector_score import get_sector_score_for_stock
        score = get_sector_score_for_stock("600519")
        assert 0 <= score <= 100

    def test_get_sector_score_unknown(self):
        """未知股票返回 50"""
        from lib.sector_score import get_sector_score_for_stock
        score = get_sector_score_for_stock("999999")
        assert score == 50.0

    def test_score_sorted_descending(self):
        """结果按总分降序排列"""
        from lib.sector_score import compute_sector_scores
        scores = compute_sector_scores(top_n=10)
        if len(scores) >= 2:
            for i in range(len(scores) - 1):
                assert scores[i].total_score >= scores[i + 1].total_score
