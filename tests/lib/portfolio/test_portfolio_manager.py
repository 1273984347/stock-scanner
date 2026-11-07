import pytest
import numpy as np

def test_single_strategy_optimize():
    """单策略优化返回正确结构"""
    from lib.portfolio.portfolio_manager import PortfolioManager, StrategyConfig

    strategies = [
        StrategyConfig(name="momentum", weight=1.0, enabled=True),
    ]
    manager = PortfolioManager(strategies=strategies)

    candidates = [
        {"code": "000001", "score": 60.0, "name": "股票1"},
        {"code": "000002", "score": 80.0, "name": "股票2"},
    ]

    result = manager.optimize({"momentum": candidates})

    assert result.weights is not None
    assert len(result.weights) >= 1
    assert result.n_positions >= 1

def test_empty_candidates_returns_empty():
    """空候选股返回空组合"""
    from lib.portfolio.portfolio_manager import PortfolioManager, StrategyConfig

    manager = PortfolioManager(strategies=[StrategyConfig(name="test", enabled=True)])
    result = manager.optimize({"test": []})

    assert result.weights == {}

def test_total_risk_budget_check():
    """总风险预算超限时压缩"""
    from lib.portfolio.portfolio_manager import PortfolioManager, StrategyConfig

    strategies = [
        StrategyConfig(name="s1", weight=0.5, enabled=True),
        StrategyConfig(name="s2", weight=0.5, enabled=True),
    ]
    manager = PortfolioManager(strategies=strategies)

    # 两个策略各分配一半
    candidates_1 = [{"code": "000001", "score": 80.0}]
    candidates_2 = [{"code": "000002", "score": 80.0}]

    result = manager.optimize({
        "s1": candidates_1,
        "s2": candidates_2,
    })

    # 验证权重总和约为1
    total = sum(result.weights.values())
    assert abs(total - 1.0) < 1e-3