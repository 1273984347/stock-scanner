"""机会评分引擎 —— V7 核心模块

公式：机会分 = 市场状态×30% + Alpha强度×25% + 板块×20% + 个股趋势×15% + 成交量×10%

评分阈值：
- ≥80: 可做（正常仓位）
- 70-79: 观察（半仓）
- <70: 过滤（不做）

P2-1（2026-06-07）：权重从硬编码改为从 config.FactorConfig.opp_score_weights 读取
- 提供 set_opp_score_weights / get_opp_score_weights / reset_opp_score_weights API
- 默认值兜底（与历史硬编码一致）：regime=0.30, alpha=0.25, sector=0.20, trend=0.15, volume=0.10
- WEIGHTS 常量保留以兼容旧 import（返回当前生效的权重）
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

# 评分权重默认值（兜底用 — 历史硬编码值）
_DEFAULT_OPP_SCORE_WEIGHTS: Dict[str, float] = {
    "regime": 0.30,
    "alpha": 0.25,
    "sector": 0.20,
    "trend": 0.15,
    "volume": 0.10,
}

# 模块级缓存：set_opp_score_weights() 设置后，compute_opportunity_score() 立即读取
# 设为 None 表示「下次计算时从 config 拉取最新值」
_active_weights: Optional[Dict[str, float]] = None

# 兼容旧 import：WEIGHTS 是动态属性，返回当前生效的权重
# 注意：WEIGHTS 不再是 dict 字面量，需通过 WEIGHTS["x"] 访问的元素依然可用


class _WeightsProxy:
    """权重代理：保持 `from ... import WEIGHTS; WEIGHTS["regime"]` 旧用法兼容"""

    def __getitem__(self, key):
        return get_opp_score_weights()[key]

    def values(self):
        return get_opp_score_weights().values()

    def keys(self):
        return get_opp_score_weights().keys()

    def items(self):
        return get_opp_score_weights().items()

    def __iter__(self):
        return iter(get_opp_score_weights())

    def __contains__(self, key):
        return key in get_opp_score_weights()

    def __len__(self):
        return len(get_opp_score_weights())

    def __eq__(self, other):
        return dict(self) == dict(other) if hasattr(other, 'items') else False

    def __repr__(self):
        return repr(get_opp_score_weights())


WEIGHTS = _WeightsProxy()


def get_opp_score_weights(source_config=None) -> Dict[str, float]:
    """获取当前生效的机会评分权重

    优先级：
    1. 模块级缓存 `_active_weights`（被 set_opp_score_weights 设置过）
    2. source_config.opp_score_weights（调用方传入的 FactorConfig）
    3. config.FactorConfig().opp_score_weights（默认 FactorConfig 的默认值）
    4. _DEFAULT_OPP_SCORE_WEIGHTS（兜底）

    Args:
        source_config: 可选，传入 FactorConfig 实例时从中读取 opp_score_weights

    Returns:
        dict: 当前生效的权重字典
    """
    if _active_weights is not None:
        return dict(_active_weights)

    if source_config is not None and hasattr(source_config, 'opp_score_weights'):
        return dict(source_config.opp_score_weights)

    # 尝试从 config 模块拉取默认值（避免循环导入：失败时兜底）
    try:
        from config import FactorConfig
        return dict(FactorConfig().opp_score_weights)
    except Exception as e:
        logger.debug(f"从 config 拉取 opp_score_weights 失败，使用兜底: {e}")
        return dict(_DEFAULT_OPP_SCORE_WEIGHTS)


def set_opp_score_weights(weights: Dict[str, float]) -> None:
    """设置机会评分权重（覆盖默认值）

    Args:
        weights: 5 项权重 dict（regime/alpha/sector/trend/volume）
    """
    global _active_weights
    if not isinstance(weights, dict):
        raise TypeError(f"weights 必须是 dict，实际 {type(weights).__name__}")
    _active_weights = dict(weights)


def reset_opp_score_weights() -> None:
    """重置为从 config 读取（清除 set_opp_score_weights 的覆盖）"""
    global _active_weights
    _active_weights = None


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
        dict: {score, rating, breakdown, weights}
    """
    # 各维度分数（0-100）
    regime_mult = REGIME_MULTIPLIER.get(regime, 0.5)
    regime_score = regime_confidence * regime_mult * 100

    alpha_score = alpha_posterior * 100

    sector_score = max(0, min(100, sector_score))

    trend_score = _normalize_trend(trend_strength)

    vol_score = _normalize_volume(volume_ratio)

    # 读取当前生效的权重（P2-1：改为从 config 读取）
    weights = get_opp_score_weights()

    # 加权总分
    total = (
        regime_score * weights["regime"]
        + alpha_score * weights["alpha"]
        + sector_score * weights["sector"]
        + trend_score * weights["trend"]
        + vol_score * weights["volume"]
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
        "weights": weights,
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
        return {"score": 0, "rating": "过滤", "breakdown": {}, "weights": get_opp_score_weights()}

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
