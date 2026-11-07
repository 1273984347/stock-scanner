"""多周期 KDJ 共振

用日线 KDJ 三组参数替代多时间框架：
- Fast KDJ(9,3,3) — 短线节奏
- Medium KDJ(20,3,3) — 中线方向
- Slow KDJ(55,3,3) — 长线趋势

评分规则：慢线50% + 中线30% + 快线20%，三周期共振±15分

使用方式：
    from lib.strategy.multi_period_kdj import compute_multi_period_kdj
    result = compute_multi_period_kdj(high, low, close)
    # result.resonance_score [-100, 100]
"""

import pandas as pd
import numpy as np
from typing import Dict, List
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class KDJPeriodResult:
    """单周期 KDJ 结果"""
    period: int
    k: float
    d: float
    j: float
    turn: str   # "turn_up" | "turn_down" | "neutral"
    zone: str   # "oversold" | "overbought" | "neutral"


@dataclass
class MultiPeriodKDJResult:
    """多周期 KDJ 共振结果"""
    fast: KDJPeriodResult       # KDJ(9,3,3)
    medium: KDJPeriodResult     # KDJ(20,3,3)
    slow: KDJPeriodResult       # KDJ(55,3,3)
    resonance_score: float      # [-100, 100]
    resonance_type: str         # "strong_buy" | "buy" | "neutral" | "sell" | "strong_sell"
    reasons: List[str]


def _compute_single_kdj(
    high: pd.Series, low: pd.Series, close: pd.Series,
    k_period: int, d_smooth: int = 3,
) -> tuple:
    """计算单周期 KDJ，返回 (K_series, D_series, J_series)"""
    lowest = low.rolling(window=k_period, min_periods=k_period).min()
    highest = high.rolling(window=k_period, min_periods=k_period).max()
    denom = highest - lowest
    rsv = ((close - lowest) / denom.replace(0, np.nan)) * 100.0
    k = rsv.ewm(com=d_smooth - 1, adjust=False).mean()
    d = k.ewm(com=d_smooth - 1, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


def _detect_turn(j_series: pd.Series, lookback: int = 3) -> str:
    """检测 KDJ 拐头（极值区拐点）"""
    if len(j_series) < lookback + 1:
        return "neutral"
    j = j_series.iloc[-lookback - 1:]
    # 低位拐头向上
    if j.iloc[-2] < j.iloc[-3] and j.iloc[-1] > j.iloc[-2]:
        if j.iloc[-2] < 20:
            return "turn_up"
    # 高位拐头向下
    if j.iloc[-2] > j.iloc[-3] and j.iloc[-1] < j.iloc[-2]:
        if j.iloc[-2] > 80:
            return "turn_down"
    return "neutral"


def _classify_zone(j_val: float) -> str:
    """J 值分区"""
    if j_val < 20:
        return "oversold"
    elif j_val > 80:
        return "overbought"
    return "neutral"


def compute_multi_period_kdj(
    high: pd.Series, low: pd.Series, close: pd.Series,
    periods: tuple = (9, 20, 55),
    d_smooth: int = 3,
) -> MultiPeriodKDJResult:
    """计算多周期 KDJ 共振

    Args:
        high, low, close: OHLC 价格序列
        periods: (快线周期, 中线周期, 慢线周期)
        d_smooth: D 平滑参数

    Returns:
        MultiPeriodKDJResult，含 resonance_score [-100, 100]
    """
    results = []
    for period in periods:
        k_s, d_s, j_s = _compute_single_kdj(high, low, close, period, d_smooth)
        k_val = float(k_s.iloc[-1]) if not pd.isna(k_s.iloc[-1]) else 50.0
        d_val = float(d_s.iloc[-1]) if not pd.isna(d_s.iloc[-1]) else 50.0
        j_val = float(j_s.iloc[-1]) if not pd.isna(j_s.iloc[-1]) else 50.0
        turn = _detect_turn(j_s)
        zone = _classify_zone(j_val)
        results.append(KDJPeriodResult(
            period=period, k=k_val, d=d_val, j=j_val,
            turn=turn, zone=zone,
        ))

    fast, medium, slow = results[0], results[1], results[2]

    # 共振评分
    score = 0.0
    reasons = []

    # 慢线（趋势方向）— 权重最高
    if slow.k > slow.d:
        score += 30
        reasons.append(f"慢线KDJ偏多(K={slow.k:.0f}>D={slow.d:.0f})")
    else:
        score -= 20
        reasons.append(f"慢线KDJ偏空(K={slow.k:.0f}<D={slow.d:.0f})")
    if slow.turn == "turn_up":
        score += 20
        reasons.append("慢线拐头向上")
    elif slow.turn == "turn_down":
        score -= 15
        reasons.append("慢线拐头向下")

    # 中线（节奏）— 中等权重
    if medium.k > medium.d:
        score += 15
        reasons.append("中线KDJ偏多")
    else:
        score -= 10
    if medium.turn == "turn_up":
        score += 10
        reasons.append("中线拐头向上")
    elif medium.turn == "turn_down":
        score -= 8

    # 快线（执行）— 最低权重
    if fast.k > fast.d:
        score += 5
    else:
        score -= 5
    if fast.turn == "turn_up":
        score += 5
        reasons.append("短线拐头向上")
    elif fast.turn == "turn_down":
        score -= 5

    # 三周期共振加成
    all_bullish = fast.k > fast.d and medium.k > medium.d and slow.k > slow.d
    all_bearish = fast.k < fast.d and medium.k < medium.d and slow.k < slow.d
    if all_bullish:
        score += 15
        reasons.append("三周期共振偏多(+15)")
    elif all_bearish:
        score -= 15
        reasons.append("三周期共振偏空(-15)")

    # 超买超卖惩罚
    if slow.zone == "overbought" and medium.zone == "overbought":
        score -= 10
        reasons.append("慢线+中线超买(-10)")
    elif slow.zone == "oversold" and medium.zone == "oversold":
        score += 10
        reasons.append("慢线+中线超卖(+10)")

    score = max(-100, min(100, score))

    if score >= 60:
        resonance_type = "strong_buy"
    elif score >= 25:
        resonance_type = "buy"
    elif score >= -25:
        resonance_type = "neutral"
    elif score >= -60:
        resonance_type = "sell"
    else:
        resonance_type = "strong_sell"

    return MultiPeriodKDJResult(
        fast=fast, medium=medium, slow=slow,
        resonance_score=round(score, 1),
        resonance_type=resonance_type,
        reasons=reasons,
    )
