# tests/lib/ic/test_panel_builder.py
import pytest, json
from pathlib import Path

def test_build_ic_panel_single_candidate():
    """单只候选股的因子面板构建"""
    from lib.ic.panel_builder import build_ic_panel

    # 模拟候选股数据（一只股票）
    candidates = [{"code": "000001", "name": "平安银行"}]

    # 模拟历史价格数据（至少30天）
    import pandas as pd
    import numpy as np
    dates = pd.date_range("2026-04-01", periods=35, freq="B")
    np.random.seed(42)
    close = 10 + np.cumsum(np.random.randn(35) * 0.1)
    hist_data = {
        "000001": pd.DataFrame({
            "date": dates,
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": np.random.rand(35) * 1e8,
        })
    }

    panel = build_ic_panel("2026-05-24", candidates, hist_data)

    assert panel["date"] == "2026-05-24"
    assert len(panel["stocks"]) == 1
    assert "momentum_5" in panel["stocks"][0]
    assert "excess_1d" in panel["stocks"][0]
    assert "excess_5d" in panel["stocks"][0]
    assert panel["stocks"][0]["momentum_5"] is not None