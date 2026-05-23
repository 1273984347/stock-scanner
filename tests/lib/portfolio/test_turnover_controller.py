import pytest
import numpy as np

def test_turnover_within_limit():
    """换手在限制内时不做调整"""
    from lib.portfolio_optimizer import TurnoverController, TurnoverConfig

    new_weights = np.array([0.4, 0.3, 0.3])
    old_weights = np.array([0.3, 0.4, 0.3])
    config = TurnoverConfig(turnover_max=0.50)
    controller = TurnoverController(config)

    result = controller.apply_turnover_penalty(new_weights, old_weights)

    np.testing.assert_allclose(result, new_weights, rtol=1e-5)

def test_turnover_exceeds_limit():
    """换手超过上限时被压缩"""
    from lib.portfolio_optimizer import TurnoverController, TurnoverConfig

    new_weights = np.array([0.8, 0.1, 0.1])
    old_weights = np.array([0.1, 0.8, 0.1])
    config = TurnoverConfig(turnover_max=0.20)
    controller = TurnoverController(config)

    result = controller.apply_turnover_penalty(new_weights, old_weights)

    # 换手被压缩后，总换手应等于上限
    turnover = np.abs(result - old_weights).sum() / 2
    assert turnover <= config.turnover_max + 1e-6

def test_detect_market_regime():
    """市场状态检测"""
    from lib.portfolio_optimizer import TurnoverController

    controller = TurnoverController()

    assert controller.detect_market_regime(0.8, 0.10) == "trending"
    assert controller.detect_market_regime(0.5, 0.30) == "volatile"
    assert controller.detect_market_regime(0.5, 0.15) == "stable"

def test_volatile_reduces_turnover():
    """高波动市场降低换手"""
    from lib.portfolio_optimizer import TurnoverController

    controller = TurnoverController()

    new_weights = np.array([0.6, 0.2, 0.2])
    old_weights = np.array([0.2, 0.6, 0.2])

    # 高波动市场
    result = controller.apply_with_regime(
        new_weights, old_weights,
        regime="volatile"
    )
    turnover = np.abs(result - old_weights).sum() / 2
    original_turnover = np.abs(new_weights - old_weights).sum() / 2
    assert turnover < original_turnover
