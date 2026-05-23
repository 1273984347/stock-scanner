import pytest
import numpy as np

def test_volatility_control():
    """波动率超限时等比例压缩"""
    from lib.portfolio_optimizer import RiskBudgetController, RiskBudgetConfig

    weights = np.array([0.5, 0.5])
    expected_returns = np.array([0.10, 0.05])
    cov_matrix = np.array([[0.04, 0.01], [0.01, 0.02]])  # 年化

    config = RiskBudgetConfig(volatility_target=0.10)  # 目标10%
    controller = RiskBudgetController(config)

    result = controller.apply_risk_controls(weights, expected_returns, cov_matrix)

    # 压缩后权重之和应小于原始
    assert np.sum(result) < np.sum(weights)

def test_var_estimation():
    """VaR估算接口存在"""
    from lib.portfolio_optimizer import RiskBudgetController, RiskBudgetConfig

    config = RiskBudgetConfig()
    controller = RiskBudgetController(config)

    # 验证接口可用（默认正态分布近似）
    vol = 0.20
    var_est = controller._estimate_var_normal(vol)
    assert var_est == pytest.approx(0.20 * 1.65, rel=0.01)

def test_drawdown_estimation():
    """回撤估算接口存在"""
    from lib.portfolio_optimizer import RiskBudgetController, RiskBudgetConfig

    config = RiskBudgetConfig()
    controller = RiskBudgetController(config)

    vol = 0.20
    dd_est = controller._estimate_drawdown_normal(vol)
    assert dd_est == pytest.approx(0.20 * 2.5, rel=0.01)
