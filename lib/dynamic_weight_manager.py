"""动态权重管理 —— 市场状态驱动的权重自适应

三源权重调整：
1. Regime 预设：6 种市场状态 × 策略权重预设表
2. IC 因子乘数：6 种状态 × 因子权重调整系数
3. 贝叶斯学习：接入 BayesianUpdater，基于交易结果微调

使用方式：
    from lib.dynamic_weight_manager import DynamicWeightManager
    manager = DynamicWeightManager()
    result = manager.compute_weights(regime="TREND_UP")
    # result.strategy_weights, result.ic_factor_weights
"""

import logging
import numpy as np
from typing import Dict, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# Regime → 策略权重预设
REGIME_STRATEGY_PRESETS: Dict[str, Dict[str, float]] = {
    "TREND_UP": {
        "trend": 0.20, "mean_reversion": 0.05, "momentum": 0.25,
        "volatility": 0.10, "stat_arb": 0.05, "trend_breakout": 0.35,
    },
    "MEAN_REVERSION": {
        "trend": 0.05, "mean_reversion": 0.30, "momentum": 0.10,
        "volatility": 0.15, "stat_arb": 0.20, "trend_breakout": 0.20,
    },
    "HIGH_VOLATILITY": {
        "trend": 0.05, "mean_reversion": 0.15, "momentum": 0.05,
        "volatility": 0.35, "stat_arb": 0.25, "trend_breakout": 0.15,
    },
    "LOW_VOLATILITY": {
        "trend": 0.15, "mean_reversion": 0.10, "momentum": 0.20,
        "volatility": 0.10, "stat_arb": 0.10, "trend_breakout": 0.35,
    },
    "TREND_DOWN": {
        "trend": 0.00, "mean_reversion": 0.20, "momentum": 0.00,
        "volatility": 0.30, "stat_arb": 0.30, "trend_breakout": 0.20,
    },
    "RISK_OFF": {
        "trend": 0.00, "mean_reversion": 0.00, "momentum": 0.00,
        "volatility": 0.50, "stat_arb": 0.50, "trend_breakout": 0.00,
    },
}

# Regime → IC 因子权重乘数
REGIME_IC_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "TREND_UP": {
        "volume_ratio": 1.2, "adx": 1.3, "trend_strength": 1.3,
        "momentum_20": 1.2, "rsi_14": 0.9, "kdj_j": 0.9,
    },
    "MEAN_REVERSION": {
        "volume_ratio": 1.0, "adx": 0.8, "trend_strength": 0.7,
        "momentum_20": 0.8, "rsi_14": 1.3, "kdj_j": 1.3,
        "williams_r": 1.3, "cci": 1.2,
    },
    "HIGH_VOLATILITY": {
        "volume_ratio": 1.1, "adx": 0.9, "volatility_20": 1.5,
        "rsi_14": 1.1, "kdj_j": 1.1,
    },
    "LOW_VOLATILITY": {
        "volume_ratio": 1.2, "adx": 1.1, "volatility_20": 0.8,
        "trend_strength": 1.2, "momentum_20": 1.2,
    },
    "TREND_DOWN": {
        "volume_ratio": 0.8, "adx": 0.7, "trend_strength": 0.5,
        "momentum_20": 0.5, "volatility_20": 1.3,
    },
    "RISK_OFF": {
        "volume_ratio": 0.7, "adx": 0.5, "trend_strength": 0.3,
        "momentum_20": 0.3, "volatility_20": 1.5,
    },
}


@dataclass
class DynamicWeightResult:
    """动态权重计算结果"""
    strategy_weights: Dict[str, float]
    ic_factor_weights: Dict[str, float]
    regime: str
    bayesian_adjustments: Dict[str, float] = field(default_factory=dict)
    source: str = "regime_preset"


class DynamicWeightManager:
    """动态权重管理器

    用法：
        manager = DynamicWeightManager()
        result = manager.compute_weights(regime="TREND_UP")
    """

    def __init__(self, bayesian_updater=None):
        self._bayesian = bayesian_updater

    def compute_weights(
        self,
        regime: str,
        base_strategy_weights: Dict[str, float] = None,
        base_ic_weights: Dict[str, float] = None,
        sector_score: float = 50.0,
        blend_ratio: float = 0.7,
    ) -> DynamicWeightResult:
        """计算当前市场条件下的动态权重

        Args:
            regime: 市场状态
            base_strategy_weights: 基准策略权重（默认用 factors.STRATEGY_WEIGHTS）
            base_ic_weights: 基准 IC 因子权重（默认用 factors.IC_FACTOR_WEIGHTS）
            sector_score: 板块评分 0-100
            blend_ratio: 向 regime 预设靠拢的程度（0.0=纯基准, 1.0=纯预设）
        """
        if base_strategy_weights is None:
            from factors import STRATEGY_WEIGHTS
            base_strategy_weights = dict(STRATEGY_WEIGHTS)
        if base_ic_weights is None:
            from factors import IC_FACTOR_WEIGHTS
            base_ic_weights = dict(IC_FACTOR_WEIGHTS)

        # 1. Regime 预设
        regime_preset = REGIME_STRATEGY_PRESETS.get(regime, base_strategy_weights)

        # 2. 混合基准 + 预设
        blended = {}
        all_keys = set(list(base_strategy_weights.keys()) + list(regime_preset.keys()))
        for key in all_keys:
            base_val = base_strategy_weights.get(key, 0.0)
            regime_val = regime_preset.get(key, 0.0)
            blended[key] = base_val * (1 - blend_ratio) + regime_val * blend_ratio

        total = sum(blended.values())
        if total > 0:
            blended = {k: v / total for k, v in blended.items()}

        # 3. 贝叶斯调整
        bayesian_adj = {}
        if self._bayesian is not None:
            for strategy_name in blended:
                adj = self._bayesian.get_weight_adjustment(
                    market_state=regime,
                    sector_bucket=self._sector_bucket(sector_score),
                    signal_type=strategy_name,
                )
                bayesian_adj[strategy_name] = adj
                blended[strategy_name] *= adj

            total = sum(blended.values())
            if total > 0:
                blended = {k: round(v / total, 4) for k, v in blended.items()}

        # 4. IC 因子权重按 regime 调整
        regime_mults = REGIME_IC_MULTIPLIERS.get(regime, {})
        adjusted_ic = dict(base_ic_weights)
        for factor, mult in regime_mults.items():
            if factor in adjusted_ic:
                adjusted_ic[factor] *= mult

        total_ic = sum(adjusted_ic.values())
        if total_ic > 0:
            adjusted_ic = {k: round(v / total_ic, 4) for k, v in adjusted_ic.items()}

        source = "regime_bayesian_blend" if bayesian_adj else "regime_preset"

        return DynamicWeightResult(
            strategy_weights=blended,
            ic_factor_weights=adjusted_ic,
            regime=regime,
            bayesian_adjustments=bayesian_adj,
            source=source,
        )

    @staticmethod
    def _sector_bucket(score: float) -> str:
        if score >= 70:
            return "high"
        elif score >= 30:
            return "mid"
        return "low"
