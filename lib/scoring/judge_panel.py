"""评委团投票制 —— 投票汇总 + ML 评委路径

P0 #13 修复 (2026-06-08, lib-incremental-audit §2.3):
- 旧实现 51 评委 7 组 150+ if/elif 硬编码规则，0 walk-forward 验证 = 必然过拟合
- 文档自承"借鉴 wbh604/UZI-Skill (1.5k stars)" 但未做 IS/OOS 验证
- 修复: JUDGE_GROUPS 中 6 组硬编码规则 weight = 0 (禁用), 仅保留 "AI 量化" 组的 ML 评委
- 实时评分可改用 evaluate_with_ml() 走纯 ML 路径 (_evaluate_ml)
- evaluate_all() 现在只跑 _evaluate_ml

Phase 7 (P7-2, 2026-11-12): 6 个 _evaluate_* 死方法 + _fund_data_missing 已删除
（自 P0#13 起零调用方；清单与残留扫描见 docs/refactor/MIGRATION-GUIDE.md BC-09）。
_JUDGE_GROUPS 默认表保留（config/judges.json 缺失时的 fallback 语义不变）。

来源：wbh604/UZI-Skill (GitHub 1.5k stars) - 借鉴但已禁用
"""
import json
from pathlib import Path

import pandas as pd
from typing import Dict, List
from dataclasses import dataclass
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


# 评委组配置 — 优先从 config/judges.json 加载，不存在则用内置默认值
_JUDGES_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "judges.json"

_DEFAULT_JUDGE_GROUPS = {
    # P0 #13 修复: 6 组硬编码规则 weight = 0 (禁用, 避免过拟合)
    "经典价值": {"weight": 0.0, "judges": [  # P0 #13 修复 (2026-06-08): 禁用
        "Benjamin Graham", "Warren Buffett", "Charlie Munger",
        "Seth Klarman", "Joel Greenblatt",
    ]},
    "成长": {"weight": 0.0, "judges": [  # P0 #13 修复 (2026-06-08): 禁用
        "Philip Fisher", "Peter Lynch", "T. Rowe Price",
        "William O'Neil", "Mark Minervini",
    ]},
    "宏观对冲": {"weight": 0.0, "judges": [  # P0 #13 修复 (2026-06-08): 禁用
        "George Soros", "Ray Dalio", "Stanley Druckenmiller",
        "Paul Tudor Jones", "David Tepper",
    ]},
    "技术趋势": {"weight": 0.0, "judges": [  # P0 #13 修复 (2026-06-08): 禁用
        "Jesse Livermore", "Richard Wyckoff", "Nicolas Darvas",
        "Larry Williams", "Marty Schwartz",
    ]},
    "中国价投": {"weight": 0.0, "judges": [  # P0 #13 修复 (2026-06-08): 禁用
        "但斌", "张坤", "刘彦春",
        "林园", "李驰",
    ]},
    "A股游资": {"weight": 0.0, "judges": [  # P0 #13 修复 (2026-06-08): 禁用
        "asking ( Asking)", "炒股养家", "赵老哥",
        "章盟主", "方新侠",
    ]},
    "AI 量化": {"weight": 1.00, "judges": [  # 仅 ML 评委启用
        "XGBoost-5d", "LightGBM-20d", "Ensemble",
    ]},
}


def _load_judge_groups() -> Dict:
    """加载评委组配置，优先从 JSON 文件读取"""
    if _JUDGES_CONFIG_PATH.exists():
        with open(_JUDGES_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return _DEFAULT_JUDGE_GROUPS


JUDGE_GROUPS = _load_judge_groups()


class JudgePanel:
    """评委团"""

    def __init__(self, factors: Dict = None, fundamentals: Dict = None,
                 market_data: pd.DataFrame = None):
        self.factors = factors or {}
        self.fundamentals = fundamentals or {}
        self.market_data = market_data
        self.results: List[VoteResult] = []

    def evaluate_all(self) -> Dict:
        """执行所有评委评估

        §4.2 P0 修复 (2026-06-08, lib-incremental-audit §2.3):
        - 旧 6 组硬编码规则 (经典价值/成长/宏观对冲/技术趋势/中国价投/A股游资)
          已被 06-08 早间 (commit 938933d) 设 weight=0 禁用
        - 6 个 _evaluate_xxx 方法体 仍有 150+ if/elif 硬编码规则
        - 评估: 6 方法已 weight=0 等价于无操作, 但仍被调用 (计算浪费 + 过拟合隐患)
        - 修复: evaluate_all 不再调 6 _evaluate_xxx, 仅靠 evaluate_with_ml 加 ML 投票
        - Phase 7 (P7-2, 2026-11-12): 6 个 _evaluate_xxx 死方法已删除（零调用方）
        """
        self.results = []
        # §4.2 修复: 6 组硬编码规则禁用 (weight=0)
        # 实际投票仅来自 ML (evaluate_with_ml 调 _evaluate_ml)
        return self.get_summary()

    def evaluate_with_ml(self, ml_prediction: dict = None) -> dict:
        """执行所有评委评估（含 ML 预测）"""
        result = self.evaluate_all()
        if ml_prediction and ml_prediction.get("model_available", False):
            ml_votes = self._evaluate_ml(ml_prediction)
            self.results.extend(ml_votes)
            result = self.get_summary()
        return result

    def _evaluate_ml(self, ml_pred: dict) -> list[VoteResult]:
        """ML 模型评委投票"""
        votes = []
        consensus = ml_pred.get("consensus", "HOLD")
        confidence = ml_pred.get("confidence", 0.5)
        score = ml_pred.get("score", 0)
        top3 = ml_pred.get("top3_factors", [])
        top3_str = " | ".join([f"{t['name']}={t['value']}" for t in top3]) if top3 else ""

        action = "BUY" if consensus == "BUY" else "SELL" if consensus == "SELL" else "HOLD"

        votes.append(VoteResult(
            judge="XGBoost-5d", group="AI 量化",
            action=action,
            confidence=min(confidence + 0.05, 1.0),
            reason=f"5日排名评分={score:.3f}" + (f" | {top3_str}" if top3_str else ""),
        ))
        votes.append(VoteResult(
            judge="LightGBM-20d", group="AI 量化",
            action=action,
            confidence=confidence,
            reason=f"20日排名评分={score:.3f}" + (f" | {top3_str}" if top3_str else ""),
        ))
        votes.append(VoteResult(
            judge="Ensemble", group="AI 量化",
            action=action,
            confidence=min(confidence + 0.02, 1.0),
            reason=f"多模型融合 score={score:.3f}",
        ))

        return votes

    def get_summary(self) -> Dict:
        """获取投票汇总"""
        if not self.results:
            return {"consensus": "HOLD", "score": 0, "bullish_pct": 0, "total_votes": 0}

        # 按评委权重加权（跳过 ABSTAIN）
        total_weight = 0
        weighted_score = 0
        group_scores = {}
        active_votes = [v for v in self.results if v.action != "ABSTAIN"]

        for vote in active_votes:
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

        # 按组统计汇总（用于输出）
        all_votes = self.results
        bullish_count = sum(1 for v in all_votes if v.action == "BUY")
        total_votes = len(active_votes)

        return {
            "consensus": consensus,
            "score": round(final_score, 3),
            "bullish_pct": round(bullish_count / total_votes * 100, 1) if total_votes > 0 else 0,
            "total_votes": total_votes,
            "bullish_count": bullish_count,
            "group_scores": {k: round(v["score"] / max(v["count"], 1), 3)
                             for k, v in group_scores.items()},
            "abstained": sum(1 for v in self.results if v.action == "ABSTAIN"),
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

