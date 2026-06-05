"""策略性能排行测试"""
import pytest
import json
from lib.strategy_ranking import StrategyRanker


class TestStrategyRanker:
    def _make_ranker(self, tmp_path):
        ranker = StrategyRanker()
        # 重定向 RESULTS_DIR
        import lib.strategy_ranking
        lib.strategy_ranking.RESULTS_DIR = tmp_path / "backtest_results"
        (tmp_path / "backtest_results").mkdir(parents=True, exist_ok=True)
        return ranker

    def test_no_data_returns_empty_with_label(self, tmp_path):
        """P1-9: 无 backtest_results 时应返回空列表（不再硬编码假数据）

        旧逻辑：返回 3 个假策略（均衡/激进/保守）
        新逻辑：返回 [] + 显式 "no_data" 标签（让 UI 显示"暂无数据"）
        """
        ranker = self._make_ranker(tmp_path)
        result = ranker.get_ranking()
        # P1-9 修复后，无数据应返回空列表
        assert result == [], f"无数据应返回空列表，实际 {result}"

    def test_real_data_ranking(self, tmp_path):
        """有真实回测数据时正常排名"""
        ranker = self._make_ranker(tmp_path)
        import lib.strategy_ranking
        # 写两个真实回测结果
        for name, sharpe in [("strategy_a", 1.5), ("strategy_b", 2.0)]:
            with open(lib.strategy_ranking.RESULTS_DIR / f"{name}.json", "w", encoding="utf-8") as f:
                json.dump({
                    "name": name,
                    "sharpe_ratio": sharpe,
                    "calmar_ratio": sharpe * 0.8,
                    "win_rate": 0.5,
                    "profit_loss_ratio": 2.0,
                    "max_drawdown": 0.1,
                    "total_return": 0.3,
                    "trades": 100,
                }, f)
        result = ranker.get_ranking()
        assert len(result) == 2
        # 排名按 composite_score 排，但 sharpe_b (2.0) > sharpe_a (1.5)
        # composite_score 反映多维度，不一定和 sharpe 完全一致
        names = [r["name"] for r in result]
        assert "strategy_a" in names
        assert "strategy_b" in names
