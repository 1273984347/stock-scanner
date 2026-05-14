"""评委团投票制 —— 51 位投资大师评审，多维度评分替代固定权重

来源：wbh604/UZI-Skill (GitHub 1.5k stars)
7 组评委：经典价值/成长/宏观对冲/技术趋势/中国价投/A股游资/AI 量化
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
    "AI 量化": {"weight": 0.20, "judges": [
        "XGBoost-5d", "LightGBM-20d", "Ensemble",
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
        }

        for group, func in groups.items():
            votes = func()
            self.results.extend(votes)

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

        # Munger: 优质公司 — 高ROE + 高毛利 + 合理PE
        if roe is not None and roe > 0.15 and pe < 25:
            margin = fund.get("profitability", {}).get("gross_margin", 0)
            if margin and margin > 0.3:
                votes.append(VoteResult("Charlie Munger", "经典价值", "BUY", 0.8,
                                        f"优质: ROE={roe:.1%} 毛利率={margin:.1%}"))
            else:
                votes.append(VoteResult("Charlie Munger", "经典价值", "HOLD", 0.55,
                                        f"ROE好但毛利一般"))
        elif roe is not None and roe < 0.08:
            votes.append(VoteResult("Charlie Munger", "经典价值", "SELL", 0.6,
                                    f"ROE过低: {roe:.1%}"))
        else:
            votes.append(VoteResult("Charlie Munger", "经典价值", "HOLD", 0.5))

        # Greenblatt: 神奇公式 — 高资本回报率 + 低PE
        roc = roe  # 简化：用ROE近似ROC
        if pe > 0 and roc is not None and roc > 0.15 and pe < 15:
            votes.append(VoteResult("Joel Greenblatt", "经典价值", "BUY", 0.8,
                                    f"神奇公式: ROC≈{roc:.1%} PE={pe:.0f}"))
        elif pe > 30 or (roc is not None and roc < 0.08):
            votes.append(VoteResult("Joel Greenblatt", "经典价值", "SELL", 0.6,
                                    f"资本回报低或PE过高"))
        else:
            votes.append(VoteResult("Joel Greenblatt", "经典价值", "HOLD", 0.5))

        return votes

    def _evaluate_growth(self) -> List[VoteResult]:
        """成长派 —— Fisher/Lynch/O'Neil/Minervini/Price"""
        votes = []
        f = self.factors

        mom_5 = f.get("momentum_5", 0)
        mom_20 = f.get("momentum_20", 0)
        vol_ratio = f.get("volume_ratio", 1.0)
        rsi = f.get("rsi_14", 50)
        pe = f.get("pe_ttm", 50)
        vol_20 = f.get("volatility_20", 25)

        # Peter Lynch: PEG 思路 — 动量好 + PE 不离谱
        if 0 < pe < 25 and mom_20 > 10:
            votes.append(VoteResult("Peter Lynch", "成长", "BUY", 0.75,
                                    f"PE={pe:.0f} 合理 + 20日动量={mom_20:.1f}%"))
        elif pe > 50 and mom_20 < -5:
            votes.append(VoteResult("Peter Lynch", "成长", "SELL", 0.7,
                                    f"PE={pe:.0f} 偏高 + 动量下滑"))
        else:
            votes.append(VoteResult("Peter Lynch", "成长", "HOLD", 0.5))

        # Philip Fisher: 长期成长 — 5日动量强 + 低波动
        if mom_5 > 3 and vol_20 < 30:
            votes.append(VoteResult("Philip Fisher", "成长", "BUY", 0.7,
                                    f"动量5={mom_5:.1f}% + 低波动{vol_20:.1f}"))
        else:
            votes.append(VoteResult("Philip Fisher", "成长", "HOLD", 0.5))

        # William O'Neil: CANSLIM — 强动量 + 量能确认 + RSI 不超买
        if mom_5 > 5 and vol_ratio > 1.5 and rsi < 75:
            votes.append(VoteResult("William O'Neil", "成长", "BUY", 0.8,
                                    f"CANSLIM: 动量={mom_5:.1f}% 量比={vol_ratio:.2f} RSI={rsi:.0f}"))
        elif mom_5 < -3 and vol_ratio > 1.3:
            votes.append(VoteResult("William O'Neil", "成长", "SELL", 0.7,
                                    f"量价背离: 动量={mom_5:.1f}% 量比={vol_ratio:.2f}"))
        else:
            votes.append(VoteResult("William O'Neil", "成长", "HOLD", 0.5))

        # Mark Minervini: 趋势模板 — 均线多头 + 量能放大
        if mom_5 > 2 and mom_20 > 8 and vol_ratio > 1.2:
            votes.append(VoteResult("Mark Minervini", "成长", "BUY", 0.75,
                                    f"趋势模板: 5d={mom_5:.1f}% 20d={mom_20:.1f}% 量比={vol_ratio:.2f}"))
        else:
            votes.append(VoteResult("Mark Minervini", "成长", "HOLD", 0.5))

        # T. Rowe Price: 稳健成长 — 中等动量 + 不高波动
        if 0 < mom_20 < 20 and vol_20 < 35:
            votes.append(VoteResult("T. Rowe Price", "成长", "BUY", 0.6,
                                    f"稳健成长: 动量20d={mom_20:.1f}% 波动={vol_20:.1f}"))
        elif vol_20 > 50:
            votes.append(VoteResult("T. Rowe Price", "成长", "SELL", 0.6,
                                    f"波动过高: {vol_20:.1f}"))
        else:
            votes.append(VoteResult("T. Rowe Price", "成长", "HOLD", 0.5))

        return votes

    def _evaluate_macro(self) -> List[VoteResult]:
        """宏观对冲派 —— Soros/Dalio/Druckenmiller/Jones/Tepper"""
        votes = []
        f = self.factors

        vol_20 = f.get("volatility_20", 25)
        mom_5 = f.get("momentum_5", 0)
        mom_20 = f.get("momentum_20", 0)
        vol_ratio = f.get("volume_ratio", 1.0)
        regime = f.get("regime", "RANGE")
        rsi = f.get("rsi_14", 50)

        # George Soros: 反身性 — 趋势 + 情绪背离（极端RSI + 强趋势）
        if mom_20 > 15 and rsi > 70:
            votes.append(VoteResult("George Soros", "宏观对冲", "SELL", 0.7,
                                    f"反身性见顶: 动量={mom_20:.1f}% RSI={rsi:.0f} 过热"))
        elif mom_20 < -15 and rsi < 30:
            votes.append(VoteResult("George Soros", "宏观对冲", "BUY", 0.7,
                                    f"反身性见底: 动量={mom_20:.1f}% RSI={rsi:.0f} 恐慌"))
        else:
            votes.append(VoteResult("George Soros", "宏观对冲", "HOLD", 0.5))

        # Ray Dalio: 全天候 — 低波动环境偏多
        if vol_20 < 20:
            votes.append(VoteResult("Ray Dalio", "宏观对冲", "BUY", 0.65,
                                    f"低波动环境: vol={vol_20:.1f}，适合风险资产"))
        elif vol_20 > 40:
            votes.append(VoteResult("Ray Dalio", "宏观对冲", "SELL", 0.65,
                                    f"高波动环境: vol={vol_20:.1f}，减配风险"))
        else:
            votes.append(VoteResult("Ray Dalio", "宏观对冲", "HOLD", 0.5))

        # Stanley Druckenmiller: 非对称下注 — 高确信度才出手
        if mom_5 > 5 and vol_ratio > 2.0 and rsi < 70:
            votes.append(VoteResult("Stanley Druckenmiller", "宏观对冲", "BUY", 0.85,
                                    f"非对称机会: 动量强+量能爆发+未超买"))
        elif mom_5 < -5 and vol_ratio > 1.5:
            votes.append(VoteResult("Stanley Druckenmiller", "宏观对冲", "SELL", 0.8,
                                    f"非对称风险: 动量崩+放量"))
        else:
            votes.append(VoteResult("Stanley Druckenmiller", "宏观对冲", "HOLD", 0.5))

        # Paul Tudor Jones: 趋势跟踪 + 止损纪律
        if regime == "TREND_UP" and vol_20 < 35:
            votes.append(VoteResult("Paul Tudor Jones", "宏观对冲", "BUY", 0.7,
                                    f"上升趋势+可控波动"))
        elif regime == "TREND_DOWN":
            votes.append(VoteResult("Paul Tudor Jones", "宏观对冲", "SELL", 0.7,
                                    f"下降趋势，回避"))
        else:
            votes.append(VoteResult("Paul Tudor Jones", "宏观对冲", "HOLD", 0.5))

        # David Tepper: 逆向 — 恐慌时买入
        if rsi < 25 and regime != "TREND_DOWN":
            votes.append(VoteResult("David Tepper", "宏观对冲", "BUY", 0.75,
                                    f"恐慌买入: RSI={rsi:.0f}"))
        elif rsi > 80:
            votes.append(VoteResult("David Tepper", "宏观对冲", "SELL", 0.7,
                                    f"贪婪卖出: RSI={rsi:.0f}"))
        else:
            votes.append(VoteResult("David Tepper", "宏观对冲", "HOLD", 0.5))

        return votes

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

        # Wyckoff: 吸筹/派发 — 量价关系判断
        if mom_5 > 2 and vol_ratio < 1.0:
            votes.append(VoteResult("Richard Wyckoff", "技术趋势", "BUY", 0.65,
                                    f"缩量上涨=吸筹: 动量={mom_5:.1f}% 量比={vol_ratio:.2f}"))
        elif mom_5 < -2 and vol_ratio > 1.5:
            votes.append(VoteResult("Richard Wyckoff", "技术趋势", "SELL", 0.7,
                                    f"放量下跌=派发: 动量={mom_5:.1f}% 量比={vol_ratio:.2f}"))
        else:
            votes.append(VoteResult("Richard Wyckoff", "技术趋势", "HOLD", 0.5))

        # Darvas: 箱体突破 — 动量强 + 量能确认
        if mom_5 > 4 and vol_ratio > 1.5:
            votes.append(VoteResult("Nicolas Darvas", "技术趋势", "BUY", 0.75,
                                    f"箱体突破: 动量={mom_5:.1f}% 量比={vol_ratio:.2f}"))
        else:
            votes.append(VoteResult("Nicolas Darvas", "技术趋势", "HOLD", 0.5))

        # Schwartz: 短线动量 + 市场宽度
        if mom_5 > 3 and rsi < 75:
            votes.append(VoteResult("Marty Schwartz", "技术趋势", "BUY", 0.65,
                                    f"短线强势: 动量={mom_5:.1f}% RSI={rsi:.0f}"))
        elif mom_5 < -3 and rsi > 25:
            votes.append(VoteResult("Marty Schwartz", "技术趋势", "SELL", 0.65,
                                    f"短线弱势: 动量={mom_5:.1f}% RSI={rsi:.0f}"))
        else:
            votes.append(VoteResult("Marty Schwartz", "技术趋势", "HOLD", 0.5))

        return votes

    def _evaluate_cn_value(self) -> List[VoteResult]:
        """中国价投派 —— 但斌/张坤/刘彦春/林园/李驰"""
        votes = []
        f = self.factors
        fund = self.fundamentals

        pe = f.get("pe_ttm", 50)
        roe = fund.get("profitability", {}).get("ROE", 0)
        gross_margin = fund.get("profitability", {}).get("gross_margin", 0)
        vol_20 = f.get("volatility_20", 25)
        mom_60 = f.get("momentum_60", 0)

        # 但斌: 优质公司 + 长期持有 — 高ROE + 低波动
        if roe and roe > 0.15 and vol_20 < 25:
            votes.append(VoteResult("但斌", "中国价投", "BUY", 0.8,
                                    f"优质白马: ROE={roe:.1%} 波动={vol_20:.1f}"))
        elif roe and roe < 0.08:
            votes.append(VoteResult("但斌", "中国价投", "SELL", 0.6,
                                    f"ROE过低: {roe:.1%}"))
        else:
            votes.append(VoteResult("但斌", "中国价投", "HOLD", 0.5))

        # 张坤: 高ROE + 护城河（高毛利率）
        if roe and roe > 0.18 and gross_margin and gross_margin > 0.4:
            votes.append(VoteResult("张坤", "中国价投", "BUY", 0.8,
                                    f"护城河: ROE={roe:.1%} 毛利率={gross_margin:.1%}"))
        elif gross_margin and gross_margin < 0.2:
            votes.append(VoteResult("张坤", "中国价投", "SELL", 0.6,
                                    f"毛利率低: {gross_margin:.1%}，缺乏护城河"))
        else:
            votes.append(VoteResult("张坤", "中国价投", "HOLD", 0.5))

        # 刘彦春: 成长价值 — ROE尚可 + 有动量
        if roe and roe > 0.12 and mom_60 > 5:
            votes.append(VoteResult("刘彦春", "中国价投", "BUY", 0.65,
                                    f"成长价值: ROE={roe:.1%} + 60日动量={mom_60:.1f}%"))
        else:
            votes.append(VoteResult("刘彦春", "中国价投", "HOLD", 0.5))

        # 林园: 垄断+高毛利 — 极高毛利率
        if gross_margin and gross_margin > 0.5 and pe < 35:
            votes.append(VoteResult("林园", "中国价投", "BUY", 0.75,
                                    f"垄断特征: 毛利率={gross_margin:.1%} PE={pe:.0f}"))
        elif gross_margin and gross_margin < 0.15:
            votes.append(VoteResult("林园", "中国价投", "SELL", 0.6,
                                    f"毛利过低: {gross_margin:.1%}，无定价权"))
        else:
            votes.append(VoteResult("林园", "中国价投", "HOLD", 0.5))

        # 李驰: 低估成长 — 低PE + 有成长
        if 0 < pe < 15 and mom_20 > 0:
            votes.append(VoteResult("李驰", "中国价投", "BUY", 0.7,
                                    f"低估成长: PE={pe:.0f} + 动量正"))
        elif pe > 50:
            votes.append(VoteResult("李驰", "中国价投", "SELL", 0.6,
                                    f"估值过高: PE={pe:.0f}"))
        else:
            votes.append(VoteResult("李驰", "中国价投", "HOLD", 0.5))

        return votes

    def _evaluate_hot_money(self) -> List[VoteResult]:
        """A股游资派 —— asking/炒股养家/赵老哥/章盟主/方新侠"""
        votes = []
        f = self.factors

        mom_5 = f.get("momentum_5", 0)
        mom_20 = f.get("momentum_20", 0)
        vol_ratio = f.get("volume_ratio", 1.0)
        rsi = f.get("rsi_14", 50)
        vol_20 = f.get("volatility_20", 25)

        # asking: 情绪周期 — 强动量 + 量能 + 不超买
        if mom_5 > 3 and vol_ratio > 1.5 and rsi < 80:
            votes.append(VoteResult("asking", "A股游资", "BUY", 0.8,
                                    f"情绪高潮: 动量5={mom_5:.1f}% 量比={vol_ratio:.2f}"))
        elif mom_5 < -3 and vol_ratio > 1.3:
            votes.append(VoteResult("asking", "A股游资", "SELL", 0.75,
                                    f"情绪退潮: 动量={mom_5:.1f}% 放量"))
        else:
            votes.append(VoteResult("asking", "A股游资", "HOLD", 0.5))

        # 炒股养家: 龙头战法 — 最强动量 + 高换手
        if mom_5 > 5 and vol_ratio > 2.0:
            votes.append(VoteResult("炒股养家", "A股游资", "BUY", 0.85,
                                    f"龙头信号: 动量={mom_5:.1f}% 量比={vol_ratio:.2f}"))
        else:
            votes.append(VoteResult("炒股养家", "A股游资", "HOLD", 0.5))

        # 赵老哥: 打板 — 动量极强（接近涨停）
        if mom_5 > 7 and rsi < 85:
            votes.append(VoteResult("赵老哥", "A股游资", "BUY", 0.7,
                                    f"打板信号: 动量={mom_5:.1f}%"))
        elif rsi > 85:
            votes.append(VoteResult("赵老哥", "A股游资", "SELL", 0.65,
                                    f"过度超买: RSI={rsi:.0f}"))
        else:
            votes.append(VoteResult("赵老哥", "A股游资", "HOLD", 0.5))

        # 章盟主: 大资金跟随 — 大量比 + 中等动量
        if vol_ratio > 2.5 and mom_5 > 0:
            votes.append(VoteResult("章盟主", "A股游资", "BUY", 0.7,
                                    f"大资金介入: 量比={vol_ratio:.2f}"))
        else:
            votes.append(VoteResult("章盟主", "A股游资", "HOLD", 0.5))

        # 方新侠: 低吸 — 超卖后反弹
        if rsi < 30 and mom_5 > -2:
            votes.append(VoteResult("方新侠", "A股游资", "BUY", 0.7,
                                    f"超卖低吸: RSI={rsi:.0f} 动量企稳"))
        elif rsi > 75 and mom_5 < -3:
            votes.append(VoteResult("方新侠", "A股游资", "SELL", 0.65,
                                    f"超买回落: RSI={rsi:.0f} 动量转弱"))
        else:
            votes.append(VoteResult("方新侠", "A股游资", "HOLD", 0.5))

        return votes

    def _evaluate_quant(self) -> List[VoteResult]:
        """量化系统派 —— Simons/Shaw/Asness/Renaissance"""
        votes = []
        f = self.factors

        hurst = f.get("hurst", 0.5)
        vol_20 = f.get("volatility_20", 25)
        mom_5 = f.get("momentum_5", 0)
        mom_20 = f.get("momentum_20", 0)
        vol_ratio = f.get("volume_ratio", 1.0)
        rsi = f.get("rsi_14", 50)

        # Jim Simons: 均值回归 — Hurst<0.5 + RSI极端
        if hurst < 0.45 and rsi < 30:
            votes.append(VoteResult("Jim Simons", "量化系统", "BUY", 0.8,
                                    f"均值回归: Hurst={hurst:.2f} RSI超卖"))
        elif hurst < 0.45 and rsi > 70:
            votes.append(VoteResult("Jim Simons", "量化系统", "SELL", 0.8,
                                    f"均值回归: Hurst={hurst:.2f} RSI超买"))
        elif hurst > 0.6 and abs(mom_5) > 3:
            # 趋势持续性
            action = "BUY" if mom_5 > 0 else "SELL"
            votes.append(VoteResult("Jim Simons", "量化系统", action, 0.7,
                                    f"趋势持续: Hurst={hurst:.2f} 动量={mom_5:.1f}%"))
        else:
            votes.append(VoteResult("Jim Simons", "量化系统", "HOLD", 0.5))

        # David Shaw: 统计套利 — 波动率异常 + 量价背离
        if vol_20 > 40 and mom_5 < -3 and vol_ratio > 1.5:
            votes.append(VoteResult("David Shaw", "量化系统", "BUY", 0.65,
                                    f"波动率异常+超卖: 可能均值回归"))
        elif vol_20 > 50:
            votes.append(VoteResult("David Shaw", "量化系统", "SELL", 0.6,
                                    f"波动率过高: {vol_20:.1f}"))
        else:
            votes.append(VoteResult("David Shaw", "量化系统", "HOLD", 0.5))

        # Cliff Asness: 价值+动量因子组合
        pe = f.get("pe_ttm", 50)
        if 0 < pe < 20 and mom_20 > 5:
            votes.append(VoteResult("Cliff Asness", "量化系统", "BUY", 0.75,
                                    f"价值+动量: PE={pe:.0f} 动量20d={mom_20:.1f}%"))
        elif pe > 40 and mom_20 < -5:
            votes.append(VoteResult("Cliff Asness", "量化系统", "SELL", 0.75,
                                    f"昂贵+弱势: PE={pe:.0f} 动量={mom_20:.1f}%"))
        else:
            votes.append(VoteResult("Cliff Asness", "量化系统", "HOLD", 0.5))

        # Renaissance Quant: 多因子综合信号
        bullish_signals = 0
        if mom_5 > 2: bullish_signals += 1
        if vol_ratio > 1.3: bullish_signals += 1
        if 40 < rsi < 70: bullish_signals += 1
        if vol_20 < 30: bullish_signals += 1
        if 0 < pe < 25: bullish_signals += 1

        if bullish_signals >= 4:
            votes.append(VoteResult("Renaissance Quant", "量化系统", "BUY", 0.8,
                                    f"多因子看多: {bullish_signals}/5"))
        elif bullish_signals <= 1:
            votes.append(VoteResult("Renaissance Quant", "量化系统", "SELL", 0.7,
                                    f"多因子看空: {bullish_signals}/5"))
        else:
            votes.append(VoteResult("Renaissance Quant", "量化系统", "HOLD", 0.5))

        return votes
