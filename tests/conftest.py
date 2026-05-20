"""pytest 共享 fixtures"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging.config
import numpy as np
import pandas as pd
import pytest
from unittest.mock import Mock, patch, MagicMock


# 加载日志配置
logging.config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"), disable_existing_loggers=False)


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


# ============================================================================
# Mock Fixtures - 离线测试支持
# ============================================================================

@pytest.fixture
def mock_akshare_stock_info(monkeypatch):
    """Mock akshare stock_individual_info_em"""
    mock_df = pd.DataFrame({
        'item': ['市盈率-动态', '市净率', '总市值'],
        'value': ['15.5', '1.8', '500亿']
    })
    def fake_stock_info(symbol):
        return mock_df
    monkeypatch.setattr("akshare.stock_individual_info_em", fake_stock_info)


@pytest.fixture
def mock_akshare_hist(monkeypatch):
    """Mock akshare stock_zh_a_hist"""
    def fake_hist(symbol, period="daily", start="2026-01-01", end="2026-01-10", adjust="qfq"):
        dates = pd.date_range(start, end, freq='D')[:10]
        return pd.DataFrame({
            '日期': dates,
            '开盘': np.random.uniform(10, 20, 10),
            '收盘': np.random.uniform(10, 20, 10),
            '最高': np.random.uniform(15, 25, 10),
            '最低': np.random.uniform(5, 15, 10),
            '成交量': np.random.uniform(1e6, 1e7, 10),
            '涨跌幅': np.random.uniform(-5, 5, 10),
        })
    monkeypatch.setattr("akshare.stock_zh_a_hist", fake_hist)


@pytest.fixture
def mock_baostock(monkeypatch):
    """Mock baostock 所有接口"""
    class MockBs:
        error_code = "0"
        def login(self): return self
        def logout(self): pass
        def query_growth_data(self, *args, **kwargs):
            class MockRs:
                error_code = "0"
                def next(self): return False
                def get_row_data(self): return ["2024", "1", "1", "1", "0.15", "0.10"]
            return MockRs()
        def query_balance_data(self, *args, **kwargs):
            class MockRs:
                error_code = "0"
                def next(self): return False
                def get_row_data(self): return ["2024", "1", "1", "1", "1", "100000"]
            return MockRs()
        def query_profit_data(self, *args, **kwargs):
            class MockRs:
                error_code = "0"
                def next(self): return False
                def get_row_data(self): return ["2024", "1", "1", "1", "1", "0.15"]
            return MockRs()
        def query_operation_data(self, *args, **kwargs):
            class MockRs:
                error_code = "0"
                def next(self): return False
                def get_row_data(self): return ["2024", "1", "1", "1", "1", "0.10"]
            return MockRs()
        def query_cash_flow_data(self, *args, **kwargs):
            class MockRs:
                error_code = "0"
                def next(self): return False
                def get_row_data(self): return ["2024", "1", "1", "1", "1", "50000"]
            return MockRs()
    monkeypatch.setattr("baostock", MockBs())
