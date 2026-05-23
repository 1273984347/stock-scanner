import pytest
import numpy as np

def test_allocate_basic():
    """单策略基本分配流程"""
    from lib.portfolio_optimizer import (
        StrategyAllocator, StrategyConfig,
        PositionSizerConfig, RiskBudgetConfig,
    )

    candidates = [
        {"code": "000001", "score": 60.0, "name": "股票1"},
        {"code": "000002", "score": 80.0, "name": "股票2"},
        {"code": "000003", "score": 100.0, "name": "股票3"},
    ]

    allocator = StrategyAllocator(
        strategy_config=StrategyConfig(name="test", weight=1.0),
        position_sizer_config=PositionSizerConfig(mapping_alpha=1.5),
        risk_budget_config=RiskBudgetConfig(volatility_target=0.20),
    )

    result = allocator.allocate(candidates)

    assert result is not None
    assert len(result.weights) == 3
    assert abs(sum(result.weights.values()) - 1.0) < 1e-3

def test_skips_disabled_strategy():
    """禁用策略返回空结果"""
    from lib.portfolio_optimizer import StrategyAllocator, StrategyConfig

    allocator = StrategyAllocator(
        strategy_config=StrategyConfig(name="disabled", weight=0.0, enabled=False),
    )
    result = allocator.allocate([])
    assert result.weights == {}
