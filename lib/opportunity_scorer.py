"""机会评分引擎 —— V7 核心模块

公式：机会分 = 市场状态×30% + Alpha强度×25% + 板块×20% + 个股趋势×15% + 成交量×10%

评分阈值：
- ≥80: 可做（正常仓位）
- 70-79: 观察（半仓）
- <70: 过滤（不做）
"""
import numpy as np
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

# 市场状态 → 仓位系数（用于市场状态分计算）
REGIME_MULTIPLIER = {
    "TREND_UP": 1.0,
    "LOW_VOLATILITY": 0.8,
    "MEAN_REVERSION": 0.6,
    "HIGH_VOLATILITY": 0.3,
    "TREND_DOWN": 0.1,
    "RISK_OFF": 0.0,
}

# 评分权重
WEIGHTS = {
    "regime": 0.30,
    "alpha": 0.25,
    "sector": 0.20,
    "trend": 0.15,
    "volume": 0.10,
}


def _normalize_trend(trend_strength: float) -> float:
    """趋势强度归一化到 0-100

    使用 tanh 压缩：trend_strength 通常在 [-10, +10] 范围
    """
    return float(np.tanh(trend_strength / 5) * 50 + 50)


def _normalize_volume(volume_ratio: float) -> float:
    """成交量比归一化到 0-100

    volume_ratio = 5日均量 / 20日均量，通常在 [0.5, 3.0]
    """
    return float(min(volume_ratio / 2, 1.0) * 100)


def compute_opportunity_score(
    regime: str,
    regime_confidence: float,
    alpha_posterior: float,
    sector_score: float,
    trend_strength: float,
    volume_ratio: float,
) -> Dict:
    """计算机会评分

    Args:
        regime: 市场状态
        regime_confidence: 市场状态置信度 0-1
        alpha_posterior: Alpha 条件胜率 0-1（来自贝叶斯更新器）
        sector_score: 板块评分 0-100
        trend_strength: 个股趋势强度（百分比，如 5.0 表示 5%）
        volume_ratio: 成交量比（5日/20日）

    Returns:
        dict: {score, rating, breakdown}
    """
    # 各维度分数（0-100）
    regime_mult = REGIME_MULTIPLIER.get(regime, 0.5)
    regime_score = regime_confidence * regime_mult * 100

    alpha_score = alpha_posterior * 100

    sector_score = max(0, min(100, sector_score))

    trend_score = _normalize_trend(trend_strength)

    vol_score = _normalize_volume(volume_ratio)

    # 加权总分
    total = (
        regime_score * WEIGHTS["regime"]
        + alpha_score * WEIGHTS["alpha"]
        + sector_score * WEIGHTS["sector"]
        + trend_score * WEIGHTS["trend"]
        + vol_score * WEIGHTS["volume"]
    )
    total = round(max(0, min(100, total)), 1)

    # 评级
    if total >= 80:
        rating = "可做"
    elif total >= 70:
        rating = "观察"
    else:
        rating = "过滤"

    return {
        "score": total,
        "rating": rating,
        "breakdown": {
            "regime_score": round(regime_score, 1),
            "alpha_score": round(alpha_score, 1),
            "sector_score": round(sector_score, 1),
            "trend_score": round(trend_score, 1),
            "volume_score": round(vol_score, 1),
        },
        "weights": WEIGHTS,
    }


def score_from_signals(signal_result: dict, alpha_posterior: float = 0.5) -> Dict:
    """从 compute_all_signals 的结果直接计算机会分

    Args:
        signal_result: factors.compute_all_signals() 的返回值
        alpha_posterior: Alpha 条件胜率（默认 0.5，无历史数据时）

    Returns:
        dict: {score, rating, breakdown}
    """
    if not signal_result:
        return {"score": 0, "rating": "过滤", "breakdown": {}, "weights": WEIGHTS}

    regime_info = signal_result.get("regime", {})
    regime = regime_info.get("regime", "MEAN_REVERSION")
    regime_confidence = regime_info.get("confidence", 0.5)

    factors = signal_result.get("factors", {})
    trend_strength = factors.get("trend_strength", 0)
    volume_ratio = factors.get("volume_ratio", 1.0)

    data_factors = signal_result.get("data_factors", {})
    sector_score = data_factors.get("sector_score", 50)

    return compute_opportunity_score(
        regime=regime,
        regime_confidence=regime_confidence,
        alpha_posterior=alpha_posterior,
        sector_score=sector_score,
        trend_strength=trend_strength,
        volume_ratio=volume_ratio,
    )
