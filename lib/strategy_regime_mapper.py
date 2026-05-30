"""策略-状态映射 —— 连续权重替代二元开关

替代 pipeline.py 的硬过滤，每个策略获得 [0.0, 1.0] 权重。
权重用作评分乘数：高权重策略的候选股加分，低权重策略的减分但不完全剔除。

使用方式：
    from lib.strategy_regime_mapper import get_strategy_allocation, apply_strategy_weights_to_candidates
    allocation = get_strategy_allocation("TREND_UP")
"""

import logging
from typing import Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 策略名称（与 pipeline.py strategies dict 对应）
STRATEGY_ZT = "涨停板复盘"
STRATEGY_VOL_BREAK = "量价突破"
STRATEGY_HOT_BOARD = "热门板块"
STRATEGY_LHB = "龙虎榜"
STRATEGY_MULTI_FACTOR = "多因子选股"

ALL_STRATEGIES = [STRATEGY_ZT, STRATEGY_VOL_BREAK, STRATEGY_HOT_BOARD,
                  STRATEGY_LHB, STRATEGY_MULTI_FACTOR]

# Regime → 策略权重 [0.0, 1.0]
REGIME_STRATEGY_MAP: Dict[str, Dict[str, float]] = {
    "TREND_UP": {
        STRATEGY_ZT: 0.8,
        STRATEGY_VOL_BREAK: 1.0,
        STRATEGY_HOT_BOARD: 0.7,
        STRATEGY_LHB: 0.5,
        STRATEGY_MULTI_FACTOR: 1.0,
    },
    "MEAN_REVERSION": {
        STRATEGY_ZT: 0.3,
        STRATEGY_VOL_BREAK: 0.2,
        STRATEGY_HOT_BOARD: 1.0,
        STRATEGY_LHB: 1.0,
        STRATEGY_MULTI_FACTOR: 0.5,
    },
    "HIGH_VOLATILITY": {
        STRATEGY_ZT: 0.1,
        STRATEGY_VOL_BREAK: 0.1,
        STRATEGY_HOT_BOARD: 0.3,
        STRATEGY_LHB: 1.0,
        STRATEGY_MULTI_FACTOR: 0.2,
    },
    "LOW_VOLATILITY": {
        STRATEGY_ZT: 0.7,
        STRATEGY_VOL_BREAK: 0.8,
        STRATEGY_HOT_BOARD: 0.6,
        STRATEGY_LHB: 0.5,
        STRATEGY_MULTI_FACTOR: 0.9,
    },
    "TREND_DOWN": {
        STRATEGY_ZT: 0.0,
        STRATEGY_VOL_BREAK: 0.0,
        STRATEGY_HOT_BOARD: 0.2,
        STRATEGY_LHB: 0.5,
        STRATEGY_MULTI_FACTOR: 0.1,
    },
    "RISK_OFF": {
        STRATEGY_ZT: 0.0,
        STRATEGY_VOL_BREAK: 0.0,
        STRATEGY_HOT_BOARD: 0.0,
        STRATEGY_LHB: 0.3,
        STRATEGY_MULTI_FACTOR: 0.0,
    },
}

# 低于此阈值的策略直接跳过（节省 API 调用）
MIN_WEIGHT_THRESHOLD = 0.05


@dataclass
class StrategyAllocation:
    """策略分配结果"""
    weights: Dict[str, float]
    regime: str
    skipped: List[str]
    active: List[str]


def get_strategy_allocation(regime: str) -> StrategyAllocation:
    """获取指定 regime 的策略权重分配

    Args:
        regime: 市场状态字符串

    Returns:
        StrategyAllocation
    """
    weights = REGIME_STRATEGY_MAP.get(regime, {})
    if not weights:
        weights = {s: 0.5 for s in ALL_STRATEGIES}

    skipped = [s for s, w in weights.items() if w < MIN_WEIGHT_THRESHOLD]
    active = [s for s, w in weights.items() if w >= MIN_WEIGHT_THRESHOLD]

    return StrategyAllocation(
        weights=weights,
        regime=regime,
        skipped=skipped,
        active=active,
    )


def apply_strategy_weights_to_candidates(
    candidates: list,
    strategy_name: str,
    strategy_weight: float,
) -> list:
    """将策略权重作为评分乘数应用到候选股

    Args:
        candidates: 候选股列表（dict 列表）
        strategy_name: 策略名称
        strategy_weight: 权重 [0.0, 1.0]

    Returns:
        调整后的候选股列表
    """
    if not candidates or strategy_weight >= 1.0:
        return candidates

    adjusted = []
    for c in candidates:
        c = c.copy()
        for score_key in ["综合评分", "ML评分", "ML置信度", "confidence"]:
            if score_key in c:
                orig = c[score_key]
                if isinstance(orig, (int, float)):
                    c[score_key] = round(orig * strategy_weight, 4)
        c["_strategy_weight"] = strategy_weight
        adjusted.append(c)

    return adjusted
