# tests/lib/ic/test_ic_calculator.py
import pytest, json, os
from pathlib import Path

def test_update_ic_history_new_date():
    """新增一日 IC 数据"""
    # Use a temp location for isolation
    from lib.ic import ic_calculator
    orig_path = ic_calculator.IC_HISTORY_PATH
    ic_calculator.IC_HISTORY_PATH = Path("/tmp/test_ic_history.json")
    try:
        panel = {
            "date": "2026-05-24",
            "stocks": [
                {"code": "000001", "momentum_5": 0.02, "excess_1d": 0.01},
                {"code": "000002", "momentum_5": 0.03, "excess_1d": 0.015},
                {"code": "000003", "momentum_5": 0.01, "excess_1d": 0.005},
                {"code": "000004", "momentum_5": -0.01, "excess_1d": -0.005},
                {"code": "000005", "momentum_5": 0.025, "excess_1d": 0.012},
                {"code": "000006", "momentum_5": 0.015, "excess_1d": 0.008},
                {"code": "000007", "momentum_5": 0.005, "excess_1d": 0.002},
                {"code": "000008", "momentum_5": -0.005, "excess_1d": -0.002},
                {"code": "000009", "momentum_5": 0.02, "excess_1d": 0.01},
                {"code": "000010", "momentum_5": 0.03, "excess_1d": 0.015},
                {"code": "000011", "momentum_5": 0.01, "excess_1d": 0.005},
            ]
        }
        result = ic_calculator.update_ic_history(panel)
        assert "factors" in result
        assert "momentum_5" in result["factors"]
        assert result["factors"]["momentum_5"]["stats"]["n_valid_days"] >= 1
    finally:
        ic_calculator.IC_HISTORY_PATH = orig_path
        if Path("/tmp/test_ic_history.json").exists():
            Path("/tmp/test_ic_history.json").unlink()