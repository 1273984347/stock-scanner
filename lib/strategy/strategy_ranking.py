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
RESULTS_DIR = Path(__file__).parent.parent.parent / "backtest_results"


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
        """获取策略排行

        P1-9 修复（2026-06-06）：无数据时返回空列表
        - 旧：返回 3 个硬编码假策略（均衡/激进/保守）误导用户
        - 新：返回 [] + 上层 UI 显示"暂无数据，请先跑回测"
        - 注意：调用方应用 try/except 处理空列表，避免误显示旧假数据
        """
        performances = self._load_all_performances()
        if not performances:
            logger.info("无 backtest_results 数据，UI 应显示'暂无排名'")
            return []

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

    # P0 #2.3 修复 (2026-06-08, lib-incremental-audit §2.3):
    # _get_default_ranking 函数已删 (40 行硬编码假策略 均衡/激进/保守)
    # 06-06 P1-9 修复已让 get_ranking 走 [] 返回路径
    # 此函数无 caller, 保留只增噪音


# 单例
strategy_ranker = StrategyRanker()
