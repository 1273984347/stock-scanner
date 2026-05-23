import pytest

def test_build_ic_report_text():
    """IC 文字报告生成"""
    from lib.ic.ic_reporter import build_ic_report_text

    ic_stats = {
        "momentum_5": {"ic_mean": 0.031, "ir": 0.72, "win_rate": 0.68, "n_valid_days": 42},
        "reversal_5": {"ic_mean": -0.008, "ir": -0.21, "win_rate": 0.42, "n_valid_days": 42},
    }
    weight_changes = {
        "momentum_5": {"change": "up", "new_weight": 0.18, "old_weight": 0.15},
        "reversal_5": {"change": "down", "new_weight": 0.04, "old_weight": 0.05},
    }
    report = build_ic_report_text("2026-05-24", ic_stats, weight_changes)
    assert "IC 因子健康报告" in report
    assert "momentum_5" in report
    assert "reversal_5" in report
