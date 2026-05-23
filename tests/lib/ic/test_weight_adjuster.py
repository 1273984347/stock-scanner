# tests/lib/ic/test_weight_adjuster.py
import pytest
from pathlib import Path
import json, os

def test_emergency_ic_alert():
    """IC < -0.10 触发紧急降权"""
    from lib.ic import weight_adjuster
    # Override path to temp
    orig = weight_adjuster.WEIGHTS_PATH
    weight_adjuster.WEIGHTS_PATH = Path("/tmp/test_weights.json")
    try:
        weights = {"momentum_5": 0.15}
        result = weight_adjuster._apply_emergency_adjustment(weights, "momentum_5", -0.12)
        assert result["momentum_5"] == 0.075  # 降50%
        assert "emergency" in result["changes"]["momentum_5"]
    finally:
        weight_adjuster.WEIGHTS_PATH = orig

def test_conservative_decay_5days():
    """连续5天 IC<0 且胜率<50%，降20%"""
    from lib.ic import weight_adjuster
    orig = weight_adjuster.WEIGHTS_PATH
    weight_adjuster.WEIGHTS_PATH = Path("/tmp/test_weights2.json")
    try:
        preset_weights = {"momentum_5": 0.15}
        ic_stats = {
            "momentum_5": {
                "ic_mean": -0.01,
                "win_rate": 0.40,
                "n_valid_days": 5
            }
        }
        current_weights = {"momentum_5": 0.15}
        result = weight_adjuster.compute_dynamic_weights(preset_weights, ic_stats, current_weights)
        assert result["weights"]["momentum_5"] == 0.12  # 降20%
        assert result["changes"]["momentum_5"] == "down"
    finally:
        weight_adjuster.WEIGHTS_PATH = orig

def test_eliminate_at_10days():
    """连续10天 IC<0 且胜率<50%，降至下限 preset×0.20"""
    from lib.ic import weight_adjuster
    orig = weight_adjuster.WEIGHTS_PATH
    weight_adjuster.WEIGHTS_PATH = Path("/tmp/test_weights3.json")
    try:
        preset_weights = {"momentum_5": 0.15}
        ic_stats = {
            "momentum_5": {
                "ic_mean": -0.01,
                "win_rate": 0.40,
                "n_valid_days": 10
            }
        }
        current_weights = {"momentum_5": 0.15}
        result = weight_adjuster.compute_dynamic_weights(preset_weights, ic_stats, current_weights)
        assert result["weights"]["momentum_5"] == 0.03  # preset×0.20 = 0.15×0.20
        assert result["changes"]["momentum_5"] == "down"
    finally:
        weight_adjuster.WEIGHTS_PATH = orig

def test_ic_sufficient():
    """is_ic_sufficient: IC样本≥20天返回True"""
    from lib.ic.weight_adjuster import is_ic_sufficient
    ic_stats = {"momentum_5": {"n_valid_days": 20}}
    assert is_ic_sufficient(ic_stats) == True
    ic_stats2 = {"momentum_5": {"n_valid_days": 19}}
    assert is_ic_sufficient(ic_stats2) == False