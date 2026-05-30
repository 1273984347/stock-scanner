"""投资者风格 Agent —— 模拟不同投资风格的决策

4 种风格：
- 价值型 (Value): 低 PE/PB + 高股息 + 安全边际
- 成长型 (Growth): 高营收增长 + 高 ROE + 行业景气
- 动量型 (Momentum): 趋势跟踪 + 突破确认 + 量价配合
- 逆向型 (Contrarian): 超卖反弹 + 恐慌买入 + 均值回归

使用方式：
    from lib.investor_agents import AgentPanel
    panel = AgentPanel()
    votes = panel.vote(hist_df, code="600519")
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AgentVote:
    """Agent 投票"""
    agent: str
    signal: str  # "bullish" / "bearish" / "neutral"
    confidence: float  # [0, 1]
    reason: str
    weight: float = 1.0


class ValueAgent:
    """价值型投资者

    关注：低估值 + 高股息 + 安全边际
    偏好：PE < 15, PB < 2, 股息率 > 3%
    """

    def vote(self, hist_df: pd.DataFrame, code: str = None, data_factors: Dict = None) -> AgentVote:
        score = 0
        reasons = []

        # 技术面：价格在 EMA21 附近（不追高）
        if len(hist_df) >= 21:
            close = hist_df["close"].iloc[-1]
            ema21 = hist_df["close"].ewm(span=21).mean().iloc[-1]
            deviation = (close - ema21) / ema21
            if -0.05 < deviation < 0.05:
                score += 1
                reasons.append("价格贴近均线")
            elif deviation < -0.10:
                score += 1
                reasons.append("低于均线（安全边际）")

        # 基本面因子
        if data_factors:
            pe = data_factors.get("pe", 0)
            pb = data_factors.get("pb", 0)
            dividend = data_factors.get("dividend_yield", 0)

            if 0 < pe < 15:
                score += 2
                reasons.append(f"PE={pe:.1f}（低估）")
            elif 0 < pe < 25:
                score += 1

            if 0 < pb < 2:
                score += 1
                reasons.append(f"PB={pb:.1f}（低估）")

            if dividend > 0.03:
                score += 1
                reasons.append(f"股息率={dividend:.1%}")

        # 成交量：缩量企稳
        if len(hist_df) >= 20:
            vol = hist_df["volume"].iloc[-5:].mean()
            vol_avg = hist_df["volume"].iloc[-20:].mean()
            if vol_avg > 0 and vol / vol_avg < 0.8:
                score += 1
                reasons.append("缩量企稳")

        confidence = min(1.0, score / 5)
        signal = "bullish" if score >= 3 else "bearish" if score <= 0 else "neutral"

        return AgentVote(
            agent="价值型",
            signal=signal,
            confidence=confidence,
            reason="; ".join(reasons) if reasons else "无明显价值信号",
        )


class GrowthAgent:
    """成长型投资者

    关注：高增长 + 高 ROE + 行业景气
    偏好：营收增长 > 20%, ROE > 15%, 量价齐升
    """

    def vote(self, hist_df: pd.DataFrame, code: str = None, data_factors: Dict = None) -> AgentVote:
        score = 0
        reasons = []

        # 动量：近期涨幅
        if len(hist_df) >= 20:
            ret_20 = (hist_df["close"].iloc[-1] / hist_df["close"].iloc[-20] - 1) * 100
            if ret_20 > 10:
                score += 2
                reasons.append(f"20日涨幅{ret_20:.1f}%（强动量）")
            elif ret_20 > 5:
                score += 1

        # 量价齐升
        if len(hist_df) >= 10:
            price_up = hist_df["close"].iloc[-1] > hist_df["close"].iloc[-5]
            vol_up = hist_df["volume"].iloc[-5:].mean() > hist_df["volume"].iloc[-10:-5].mean()
            if price_up and vol_up:
                score += 2
                reasons.append("量价齐升")

        # 基本面
        if data_factors:
            roe = data_factors.get("roe", 0)
            if roe > 15:
                score += 1
                reasons.append(f"ROE={roe:.1f}%")

        # 趋势：EMA8 > EMA21 > EMA55
        if len(hist_df) >= 55:
            close = hist_df["close"]
            ema8 = close.ewm(span=8).mean().iloc[-1]
            ema21 = close.ewm(span=21).mean().iloc[-1]
            ema55 = close.ewm(span=55).mean().iloc[-1]
            if ema8 > ema21 > ema55:
                score += 2
                reasons.append("均线多头排列")

        confidence = min(1.0, score / 6)
        signal = "bullish" if score >= 4 else "bearish" if score <= 1 else "neutral"

        return AgentVote(
            agent="成长型",
            signal=signal,
            confidence=confidence,
            reason="; ".join(reasons) if reasons else "无明显成长信号",
        )


class MomentumAgent:
    """动量型投资者

    关注：趋势跟踪 + 突破确认 + 量价配合
    偏好：20日新高 + 放量突破 + ADX > 25
    """

    def vote(self, hist_df: pd.DataFrame, code: str = None, data_factors: Dict = None) -> AgentVote:
        score = 0
        reasons = []

        if len(hist_df) < 20:
            return AgentVote("动量型", "neutral", 0, "数据不足")

        close = hist_df["close"]
        current = close.iloc[-1]

        # 20日新高突破
        high_20 = close.iloc[-20:].max()
        if current >= high_20 * 0.98:
            score += 2
            reasons.append("接近20日新高")

        # 放量确认
        vol = hist_df["volume"]
        vol_ratio = vol.iloc[-1] / vol.iloc[-20:].mean() if vol.iloc[-20:].mean() > 0 else 1
        if vol_ratio > 1.5:
            score += 1
            reasons.append(f"放量{vol_ratio:.1f}倍")

        # ADX 趋势强度
        if data_factors:
            adx = data_factors.get("adx", 0)
            if adx > 25:
                score += 2
                reasons.append(f"ADX={adx:.0f}（强趋势）")
            elif adx > 20:
                score += 1

        # RSI 不过热
        if len(hist_df) >= 14:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - 100 / (1 + rs)
            rsi_val = rsi.iloc[-1]
            if 40 < rsi_val < 70:
                score += 1
                reasons.append(f"RSI={rsi_val:.0f}（健康区间）")

        confidence = min(1.0, score / 5)
        signal = "bullish" if score >= 3 else "bearish" if score <= 0 else "neutral"

        return AgentVote(
            agent="动量型",
            signal=signal,
            confidence=confidence,
            reason="; ".join(reasons) if reasons else "无明显动量信号",
        )


class ContrarianAgent:
    """逆向型投资者

    关注：超卖反弹 + 恐慌买入 + 均值回归
    偏好：RSI < 30 + 布林带下轨 + 连续下跌后企稳
    """

    def vote(self, hist_df: pd.DataFrame, code: str = None, data_factors: Dict = None) -> AgentVote:
        score = 0
        reasons = []

        if len(hist_df) < 20:
            return AgentVote("逆向型", "neutral", 0, "数据不足")

        close = hist_df["close"]

        # RSI 超卖
        if len(hist_df) >= 14:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - 100 / (1 + rs)
            rsi_val = rsi.iloc[-1]
            if rsi_val < 30:
                score += 2
                reasons.append(f"RSI={rsi_val:.0f}（超卖）")
            elif rsi_val < 40:
                score += 1

        # 布林带下轨
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        lower_band = sma20 - 2 * std20
        if close.iloc[-1] < lower_band.iloc[-1]:
            score += 2
            reasons.append("触及布林带下轨")

        # 连续下跌后企稳
        ret_5 = (close.iloc[-1] / close.iloc[-5] - 1) * 100
        ret_20 = (close.iloc[-1] / close.iloc[-20] - 1) * 100
        if ret_20 < -10 and ret_5 > -2:
            score += 1
            reasons.append(f"20日跌{ret_20:.1f}%后企稳")

        # 缩量（恐慌出清）
        vol = hist_df["volume"]
        if vol.iloc[-1] < vol.iloc[-20:].mean() * 0.6:
            score += 1
            reasons.append("缩量（恐慌出清）")

        confidence = min(1.0, score / 5)
        signal = "bullish" if score >= 3 else "bearish" if score <= 0 else "neutral"

        return AgentVote(
            agent="逆向型",
            signal=signal,
            confidence=confidence,
            reason="; ".join(reasons) if reasons else "无明显逆向信号",
        )


class AgentPanel:
    """评委团 —— 汇总所有 Agent 投票"""

    def __init__(self):
        self.agents = [
            (ValueAgent(), 1.0),
            (GrowthAgent(), 1.0),
            (MomentumAgent(), 1.2),  # 动量略高权重
            (ContrarianAgent(), 0.8),
        ]

    def vote(self, hist_df: pd.DataFrame, code: str = None, data_factors: Dict = None) -> Dict:
        """汇总投票

        Returns:
            dict with keys:
            - signal: "bullish" / "bearish" / "neutral"
            - confidence: [0, 1]
            - votes: list of AgentVote
            - consensus: 一致性描述
        """
        votes = []
        for agent, weight in self.agents:
            vote = agent.vote(hist_df, code, data_factors)
            vote.weight = weight
            votes.append(vote)

        # 加权投票
        bullish_score = 0
        bearish_score = 0
        total_weight = 0

        for v in votes:
            if v.signal == "bullish":
                bullish_score += v.confidence * v.weight
            elif v.signal == "bearish":
                bearish_score += v.confidence * v.weight
            total_weight += v.weight

        if total_weight == 0:
            return {"signal": "neutral", "confidence": 0, "votes": votes, "consensus": "无投票"}

        bullish_pct = bullish_score / total_weight
        bearish_pct = bearish_score / total_weight

        if bullish_pct > 0.5:
            signal = "bullish"
            confidence = bullish_pct
        elif bearish_pct > 0.5:
            signal = "bearish"
            confidence = bearish_pct
        else:
            signal = "neutral"
            confidence = max(bullish_pct, bearish_pct)

        # 一致性
        signals = [v.signal for v in votes]
        if all(s == "bullish" for s in signals):
            consensus = "全票看多"
        elif all(s == "bearish" for s in signals):
            consensus = "全票看空"
        elif signals.count("bullish") > signals.count("bearish"):
            consensus = f"多数看多({signals.count('bullish')}/{len(signals)})"
        elif signals.count("bearish") > signals.count("bullish"):
            consensus = f"多数看空({signals.count('bearish')}/{len(signals)})"
        else:
            consensus = "分歧"

        return {
            "signal": signal,
            "confidence": round(confidence, 3),
            "votes": votes,
            "consensus": consensus,
            "bullish_pct": round(bullish_pct, 3),
            "bearish_pct": round(bearish_pct, 3),
        }
