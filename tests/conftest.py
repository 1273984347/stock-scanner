"""pytest 共享 fixtures"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_ohlcv(length=60):
    """标准 OHLCV 数据，60行"""
    dates = pd.date_range("2026-01-01", periods=length, freq="D")
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(length) * 0.5)
    return pd.DataFrame({
        "date": dates,
        "open": close + np.random.randn(length) * 0.2,
        "high": close + np.abs(np.random.randn(length)) * 0.5,
        "low": close - np.abs(np.random.randn(length)) * 0.5,
        "close": close,
        "volume": np.random.randint(1e6, 5e6, length),
    })


@pytest.fixture
def sample_market_data():
    """标准市场数据字典 {code: DataFrame}"""
    dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    prices = [100, 101, 102, 103, 104]
    records = []
    pre_close = 100
    for i, d in enumerate(dates):
        c = prices[i]
        records.append({
            "trade_date": pd.Timestamp(d),
            "open": c, "close": c,
            "high": c * 1.01, "low": c * 0.99,
            "volume": 1e6,
            "pre_close": pre_close,
            "pct_chg": (c - pre_close) / pre_close * 100,
            "is_limit_up": False, "is_limit_down": False, "is_suspend": False,
        })
        pre_close = c
    return {"000001": pd.DataFrame(records)}


@pytest.fixture
def broker():
    """干净的 ABroker 实例"""
    from lib.broker import ABroker
    broker = ABroker(initial_cash=1_000_000)
    broker.set_trade_dates(["2026-01-05", "2026-01-06", "2026-01-07"])
    return broker


@pytest.fixture
def sample_realtime_row():
    """实时行情行"""
    return pd.Series({
        "代码": "000001",
        "名称": "平安银行",
        "最新价": 12.50,
        "市盈率-动态": 6.5,
        "市净率": 0.85,
        "总市值": 2.5e11,
        "量比": 1.2,
        "换手率": 0.85,
        "成交额": 5e8,
        "涨跌幅": 1.5,
    })
