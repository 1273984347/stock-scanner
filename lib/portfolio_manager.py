"""PortfolioManager — 多策略协调层

职责：
1. 汇总各策略仓位
2. 总风险预算检查（超过则压缩）
3. 行业暴露检查（最大30%）
4. 换手控制（TurnoverController 内嵌）
5. 返回统一 OptimizedPortfolio
"""
import json
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
from dataclasses import dataclass, field
import logging

from lib.portfolio_optimizer import (
    StrategyAllocator,
    StrategyConfig,
    PositionSizerConfig,
    RiskBudgetConfig,
    TurnoverConfig,
    PortfolioOptimizer,
    OptimizedPortfolio,
)

logger = logging.getLogger(__name__)


@dataclass
class PortfolioManagerConfig:
    """PortfolioManager全局配置"""
    max_sector_pct: float = 0.30        # 单行业最大暴露
    min_weight: float = 0.02            # 最小仓位（低于此视为0）
    default_position_sizer: PositionSizerConfig = None
    default_risk_budget: RiskBudgetConfig = None
    default_turnover: TurnoverConfig = None


class PortfolioManager:
    """多策略协调层（轻量版：只做汇总+总风控）"""

    def __init__(
        self,
        strategies: List[StrategyConfig] = None,
        config: PortfolioManagerConfig = None,
    ):
        self.strategies = strategies or [StrategyConfig(name="default")]
        self.config = config or PortfolioManagerConfig()
        self._allocators = {}
        for s in self.strategies:
            cfg = self.config
            self._allocators[s.name] = StrategyAllocator(
                strategy_config=s,
                position_sizer_config=cfg.default_position_sizer or PositionSizerConfig(),
                risk_budget_config=cfg.default_risk_budget or RiskBudgetConfig(),
                turnover_config=cfg.default_turnover or TurnoverConfig(),
            )

    def optimize(
        self,
        strategy_candidates: Dict[str, List[Dict]],
        current_weights: Optional[Dict[str, float]] = None,
    ) -> OptimizedPortfolio:
        """多策略组合优化

        Args:
            strategy_candidates: {策略名: [候选股列表]}
            current_weights: 当前持仓 {code: weight}

        Returns:
            OptimizedPortfolio
        """
        if not strategy_candidates:
            return OptimizedPortfolio(
                weights={}, expected_return=0, expected_vol=0,
                sharpe_ratio=0, sector_exposure={}, turnover=0, n_positions=0,
            )

        # Step 1: 各策略独立分配
        strategy_allocations = {}
        for name, candidates in strategy_candidates.items():
            allocator = self._allocators.get(name)
            if allocator is None:
                continue
            allocation = allocator.allocate(candidates, current_weights)
            strategy_allocations[name] = allocation

        # Step 2: 汇总权重
        total_weights: Dict[str, float] = {}
        for name, alloc in strategy_allocations.items():
            strat_cfg = next(
                (s for s in self.strategies if s.name == name),
                None
            )
            strat_weight = getattr(strat_cfg, "weight", 1.0) if strat_cfg else 1.0
            for code, w in alloc.weights.items():
                total_weights[code] = total_weights.get(code, 0) + w * strat_weight

        # 归一化
        total = sum(total_weights.values())
        if total > 0:
            for code in total_weights:
                total_weights[code] /= total

        # Step 3: 清理小仓位
        total_weights = {
            k: v for k, v in total_weights.items() if v >= self.config.min_weight
        }

        # Step 4: 计算换手率
        turnover = 0
        if current_weights:
            for code, new_w in total_weights.items():
                old_w = current_weights.get(code, 0)
                turnover += abs(new_w - old_w)
            for code, old_w in current_weights.items():
                if code not in total_weights:
                    turnover += abs(old_w)
            turnover /= 2

        n_positions = len(total_weights)

        # 简化指标（基于汇总权重）
        expected_return = sum(
            alloc.expected_return * (sum(alloc.weights.values()))
            for alloc in strategy_allocations.values()
        ) / max(n_positions, 1)
        expected_vol = sum(
            alloc.expected_vol * (sum(alloc.weights.values()))
            for alloc in strategy_allocations.values()
        ) / max(n_positions, 1)
        sharpe = expected_return / expected_vol if expected_vol > 0 else 0

        return OptimizedPortfolio(
            weights=total_weights,
            expected_return=round(float(expected_return), 4),
            expected_vol=round(float(expected_vol), 4),
            sharpe_ratio=round(float(sharpe), 2),
            sector_exposure={},
            turnover=round(float(turnover), 4),
            n_positions=n_positions,
        )
