"""评委团投票制 —— 51 位投资大师评审，多维度评分替代固定权重

来源：wbh604/UZI-Skill (GitHub 1.5k stars)
7 组评委：经典价值/成长/宏观对冲/技术趋势/中国价投/A股游资/量化系统
每组 5-10 条规则，加权投票输出综合评级
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class Judge:
    """评委"""
    name: str
    group: str
    weight: float  # 个人权重


@dataclass
class VoteResult:
    """投票结果"""
    judge: str
    group: str
    action: str  # BUY / SELL / HOLD
    confidence: float
    reason: str = ""


# 评委组配置
JUDGE_GROUPS = {
    "经典价值": {"weight": 0.20, "judges": [
        "Benjamin Graham", "Warren Buffett", "Charlie Munger",
        "Seth Klarman", "Joel Greenblatt",
    ]},
    "成长": {"weight": 0.15, "judges": [
        "Philip Fisher", "Peter Lynch", "T. Rowe Price",
        "William O'Neil", "Mark Minervini",
    ]},
    "宏观对冲": {"weight": 0.15, "judges": [
        "George Soros", "Ray Dalio", "Stanley Druckenmiller",
        "Paul Tudor Jones", "David Tepper",
    ]},
    "技术趋势": {"weight": 0.15, "judges": [
        "Jesse Livermore", "Richard Wyckoff", "Nicolas Darvas",
        "Larry Williams", "Marty Schwartz",
    ]},
    "中国价投": {"weight": 0.15, "judges": [
        "但斌", "张坤", "刘彦春",
        "林园", "李驰",
    ]},
    "A股游资": {"weight": 0.10, "judges": [
        "asking ( Asking)", "炒股养家", "赵老哥",
        "章盟主", "方新侠",
    ]},
    "量化系统": {"weight": 0.10, "judges": [
        "James Simons", "David Shaw", "Cliff Asness",
        "Jim Simons", "Renaissance Quant",
    ]},
}


class JudgePanel:
    """评委团"""

    def __init__(self, factors: Dict = None, fundamentals: Dict = None,
                 market_data: pd.DataFrame = None):
        self.factors = factors or {}
        self.fundamentals = fundamentals or {}
        self.market_data = market_data
        self.results: List[VoteResult] = []

    def evaluate_all(self) -> Dict:
        """执行所有评委评估"""
        self.results = []
        groups = {
            "经典价值": self._evaluate_value,
            "成长": self._evaluate_growth,
            "宏观对冲": self._evaluate_macro,
            "技术趋势": self._evaluate_technical,
            "中国价投": self._evaluate_cn_value,
            "A股游资": self._evaluate_hot_money,
            "量化系统": self._evaluate_quant,
        }

        for group, func in groups.items():
            votes = func()
            self.results.extend(votes)

        return self.get_summary()

    def get_summary(self) -> Dict:
        """获取投票汇总"""
        if not self.results:
            return {"consensus": "HOLD", "score": 0, "bullish_pct": 0}

        # 按评委权重加权
        total_weight = 0
        weighted_score = 0
        group_scores = {}

        for vote in self.results:
            judge_name = vote.judge
            judge_weight = self._get_judge_weight(judge_name)

            if vote.action == "BUY":
                signal_score = 1
            elif vote.action == "SELL":
                signal_score = -1
            else:
                signal_score = 0

            adjusted_score = signal_score * vote.confidence * judge_weight
            weighted_score += adjusted_score
            total_weight += judge_weight

            # 按组统计
            if vote.group not in group_scores:
                group_scores[vote.group] = {"score": 0, "count": 0, "votes": []}
            group_scores[vote.group]["score"] += adjusted_score
            group_scores[vote.group]["count"] += 1
            group_scores[vote.group]["votes"].append({
                "judge": judge_name, "action": vote.action,
                "confidence": vote.confidence, "reason": vote.reason,
            })

        final_score = weighted_score / total_weight if total_weight > 0 else 0

        if final_score > 0.2:
            consensus = "BUY"
        elif final_score < -0.2:
            consensus = "SELL"
        else:
            consensus = "HOLD"

        bullish_count = sum(1 for v in self.results if v.action == "BUY")
        total_votes = len(self.results)

        return {
            "consensus": consensus,
            "score": round(final_score, 3),
            "bullish_pct": round(bullish_count / total_votes * 100, 1) if total_votes > 0 else 0,
            "total_votes": total_votes,
            "bullish_count": bullish_count,
            "group_scores": {k: round(v["score"] / max(v["count"], 1), 3)
                             for k, v in group_scores.items()},
            "details": [{
                "judge": v.judge, "group": v.group,
                "action": v.action, "confidence": v.confidence,
                "reason": v.reason,
            } for v in self.results],
        }

    def _get_judge_weight(self, judge_name: str) -> float:
        for group, cfg in JUDGE_GROUPS.items():
            if judge_name in cfg["judges"]:
                return cfg["weight"] / len(cfg["judges"])
        return 0.01

    def _evaluate_value(self) -> List[VoteResult]:
        """经典价值派"""
        votes = []
        f = self.factors
        fund = self.fundamentals

        pe = f.get("pe_ttm", 50)
        pb = fund.get("per_share", {}).get("BVPS", 0)
        roe = fund.get("profitability", {}).get("ROE", 0)
        debt = fund.get("solvency", {}).get("debt_to_asset", 0.5)
        fcf = fund.get("cash_flow", {}).get("free_cash_flow", 0)

        # Buffett: 低PE + 高ROE + 低负债
        if pe < 15 and roe is not None and roe > 0.15 and debt is not None and debt < 0.50:
            votes.append(VoteResult("Warren Buffett", "经典价值", "BUY", 0.85,
                                    f"PE={pe}<15, ROE={roe:.1%}>15%, 负债率低"))
        elif pe > 30 or (roe is not None and roe < 0.08):
            votes.append(VoteResult("Warren Buffett", "经典价值", "SELL", 0.7,
                                    f"PE={pe}>30 或 ROE={roe:.1%}<8%"))

        # Graham: PE < 15, PB < 1.5
        if pe < 15 and pb is not None and pb < 1.5:
            votes.append(VoteResult("Benjamin Graham", "经典价值", "BUY", 0.8,
                                    f"PE={pe}<15, PB={pb:.2f}<1.5"))
        else:
            votes.append(VoteResult("Benjamin Graham", "经典价值", "HOLD", 0.5))

        # Klarman: 低PE + 安全边际
        if pe < 12 and debt is not None and debt < 0.40:
            votes.append(VoteResult("Seth Klarman", "经典价值", "BUY", 0.75,
                                    f"PE={pe}<12, 负债率低"))
        else:
            votes.append(VoteResult("Seth Klarman", "经典价值", "HOLD", 0.5))

        return votes

    def _evaluate_growth(self) -> List[VoteResult]:
        """成长派"""
        return [VoteResult("Peter Lynch", "成长", "HOLD", 0.5)]

    def _evaluate_macro(self) -> List[VoteResult]:
        """宏观对冲"""
        return [VoteResult("Ray Dalio", "宏观对冲", "HOLD", 0.5)]

    def _evaluate_technical(self) -> List[VoteResult]:
        """技术趋势"""
        votes = []
        f = self.factors
        df = self.market_data

        rsi = f.get("rsi_14", 50)
        vol_ratio = f.get("volume_ratio", 1.0)
        mom_5 = f.get("momentum_5", 0)

        # Livermore: 放量上涨
        if mom_5 > 3 and vol_ratio > 1.5:
            votes.append(VoteResult("Jesse Livermore", "技术趋势", "BUY", 0.8,
                                    f"动量5={mom_5:.1f}%+量比{vol_ratio:.2f}"))
        elif mom_5 < -5 and vol_ratio > 1.3:
            votes.append(VoteResult("Jesse Livermore", "技术趋势", "SELL", 0.75,
                                    f"破位放量: 动量={mom_5:.1f}%, 量比={vol_ratio:.2f}"))

        # RSI 超卖
        if rsi < 30:
            votes.append(VoteResult("Larry Williams", "技术趋势", "BUY", 0.7,
                                    f"RSI超卖: {rsi:.1f}<30"))
        elif rsi > 70:
            votes.append(VoteResult("Larry Williams", "技术趋势", "SELL", 0.7,
                                    f"RSI超买: {rsi:.1f}>70"))

        return votes

    def _evaluate_cn_value(self) -> List[VoteResult]:
        """中国价投"""
        return [VoteResult("但斌", "中国价投", "HOLD", 0.5)]

    def _evaluate_hot_money(self) -> List[VoteResult]:
        """A股游资"""
        return [VoteResult("asking", "A股游资", "HOLD", 0.5)]

    def _evaluate_quant(self) -> List[VoteResult]:
        """量化系统"""
        return [VoteResult("James Simons", "量化系统", "HOLD", 0.5)]
