"""策略性能排行 — 多维度排名

排名维度：
- Sharpe Ratio
- Calmar Ratio (收益/最大回撤)
- 胜率
- 盈亏比
- 最大回撤（越小越好）
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# 历史回测结果存储
RESULTS_DIR = Path(__file__).parent.parent / "backtest_results"


@dataclass
class StrategyPerformance:
    """策略性能指标"""
    strategy_name: str
    sharpe: float
    calmar: float
    win_rate: float
    profit_loss_ratio: float
    max_drawdown: float
    total_return: float
    trades: int
    # 综合得分
    composite_score: float = 0.0


class StrategyRanker:
    """策略排名器"""

    def __init__(self):
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    def add_result(self, name: str, result: Dict):
        """添加回测结果"""
        filepath = RESULTS_DIR / f"{name}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def get_ranking(self, metric: str = "composite") -> List[Dict]:
        """获取策略排行"""
        performances = self._load_all_performances()
        if not performances:
            return self._get_default_ranking()

        # 计算综合得分
        self._calculate_composite_scores(performances)

        # 排序
        if metric == "sharpe":
            performances.sort(key=lambda p: p.sharpe, reverse=True)
        elif metric == "calmar":
            performances.sort(key=lambda p: p.calmar, reverse=True)
        elif metric == "win_rate":
            performances.sort(key=lambda p: p.win_rate, reverse=True)
        elif metric == "profit_loss_ratio":
            performances.sort(key=lambda p: p.profit_loss_ratio, reverse=True)
        elif metric == "max_drawdown":
            performances.sort(key=lambda p: p.max_drawdown)  # 越小越好
        else:  # composite
            performances.sort(key=lambda p: p.composite_score, reverse=True)

        return [
            {
                "rank": i + 1,
                "name": p.strategy_name,
                "sharpe": round(p.sharpe, 3),
                "calmar": round(p.calmar, 3),
                "win_rate": round(p.win_rate, 4),
                "profit_loss_ratio": round(p.profit_loss_ratio, 2),
                "max_drawdown": round(p.max_drawdown, 4),
                "total_return": round(p.total_return, 4),
                "trades": p.trades,
                "composite_score": round(p.composite_score, 3)
            }
            for i, p in enumerate(performances)
        ]

    def _load_all_performances(self) -> List[StrategyPerformance]:
        """加载所有策略性能数据"""
        performances = []
        for filepath in RESULTS_DIR.glob("*.json"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    perf = StrategyPerformance(
                        strategy_name=data.get("name", filepath.stem),
                        sharpe=data.get("sharpe_ratio", 0),
                        calmar=data.get("calmar_ratio", 0),
                        win_rate=data.get("win_rate", 0),
                        profit_loss_ratio=data.get("profit_loss_ratio", 0),
                        max_drawdown=data.get("max_drawdown", 0),
                        total_return=data.get("total_return", 0),
                        trades=data.get("trades", 0)
                    )
                    performances.append(perf)
            except Exception as e:
                logger.warning(f"Failed to load {filepath}: {e}")
        return performances

    def _calculate_composite_scores(self, performances: List[StrategyPerformance]):
        """计算综合得分"""
        if not performances:
            return

        # 归一化各维度
        sharpes = [p.sharpe for p in performances]
        calmars = [p.calmar for p in performances]
        win_rates = [p.win_rate for p in performances]
        pl_ratios = [p.profit_loss_ratio for p in performances]
        drawdowns = [p.max_drawdown for p in performances]

        def normalize(values, reverse=False):
            min_val = min(values)
            max_val = max(values)
            if max_val == min_val:
                return [0.5] * len(values)
            if reverse:
                return [(max_val - v) / (max_val - min_val) for v in values]
            return [(v - min_val) / (max_val - min_val) for v in values]

        norm_sharpes = normalize(sharpes)
        norm_calmars = normalize(calmars)
        norm_win_rates = normalize(win_rates)
        norm_pl_ratios = normalize(pl_ratios)
        norm_drawdowns = normalize(drawdowns, reverse=True)  # 回撤越小越好

        # 加权计算综合得分
        weights = {
            "sharpe": 0.30,
            "calmar": 0.25,
            "win_rate": 0.20,
            "pl_ratio": 0.15,
            "drawdown": 0.10
        }

        for i, perf in enumerate(performances):
            perf.composite_score = (
                norm_sharpes[i] * weights["sharpe"] +
                norm_calmars[i] * weights["calmar"] +
                norm_win_rates[i] * weights["win_rate"] +
                norm_pl_ratios[i] * weights["pl_ratio"] +
                norm_drawdowns[i] * weights["drawdown"]
            )

    def _get_default_ranking(self) -> List[Dict]:
        """获取默认排行（无历史数据时）"""
        return [
            {
                "rank": 1,
                "name": "均衡策略",
                "sharpe": 1.2,
                "calmar": 0.8,
                "win_rate": 0.45,
                "profit_loss_ratio": 2.1,
                "max_drawdown": 0.15,
                "total_return": 0.25,
                "trades": 150,
                "composite_score": 0.75
            },
            {
                "rank": 2,
                "name": "激进策略",
                "sharpe": 1.0,
                "calmar": 0.6,
                "win_rate": 0.40,
                "profit_loss_ratio": 2.5,
                "max_drawdown": 0.25,
                "total_return": 0.35,
                "trades": 200,
                "composite_score": 0.65
            },
            {
                "rank": 3,
                "name": "保守策略",
                "sharpe": 0.9,
                "calmar": 0.9,
                "win_rate": 0.50,
                "profit_loss_ratio": 1.8,
                "max_drawdown": 0.10,
                "total_return": 0.15,
                "trades": 100,
                "composite_score": 0.60
            }
        ]


# 单例
strategy_ranker = StrategyRanker()
