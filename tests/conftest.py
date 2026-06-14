"""pytest 共享 fixtures"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging.config
import socket
import numpy as np
import pandas as pd
import pytest
from unittest.mock import Mock, patch, MagicMock


# 加载日志配置
logging.config.fileConfig(os.path.join(os.path.dirname(__file__), "logging.conf"), disable_existing_loggers=False)


# ============================================================================
# 网络隔离（修 hang 35+ min 元凶 2026-06-06）
#
# 元凶：lib/data_ashare.py + lib/data_layer.py + lib/scorecard.py 等
# 调 akshare / sina / eastmoney 真实网络 API，单测试慢（30-60s），
# 多个测试叠加 + _retry_api_call 重试 = 全量 35+ min。
#
# 修法：autouse fixture 默认 mock 这些慢 API 返回缓存数据，
# 标记 @pytest.mark.enable_socket 显式豁免。
# ============================================================================

def pytest_configure(config):
    """注册 enable_socket 标记"""
    config.addinivalue_line(
        "markers", "enable_socket: 允许此测试访问真实网络（默认禁用）"
    )


def _mock_akshare_stock_zh_a_spot():
    """ak.stock_zh_a_spot() → 模拟全市场股票列表（60+ 页 → 单页 100 只）"""
    return pd.DataFrame({
        "代码": [f"{i:06d}" for i in range(1, 101)],
        "名称": [f"股票{i}" for i in range(1, 101)],
        "最新价": np.random.uniform(5, 100, 100),
        "涨跌幅": np.random.uniform(-10, 10, 100),
        "市盈率-动态": np.random.uniform(5, 50, 100),
        "市净率": np.random.uniform(0.5, 10, 100),
        "量比": np.random.uniform(0.5, 5, 100),
        "换手率": np.random.uniform(0.1, 10, 100),
        "成交额": np.random.uniform(1e6, 1e10, 100),
    })


def _mock_akshare_stock_zh_index_daily(symbol="sh000300"):
    """ak.stock_zh_index_daily() → 模拟 HS300 60 日 K 线"""
    dates = pd.date_range("2025-01-01", periods=60, freq="D")
    np.random.seed(42)
    close = 4000 + np.cumsum(np.random.randn(60) * 20)
    return pd.DataFrame({
        "date": dates,
        "open": close - np.random.rand(60) * 10,
        "high": close + np.random.rand(60) * 20,
        "low": close - np.random.rand(60) * 20,
        "close": close,
        "volume": np.random.randint(1e8, 5e8, 60),
    })


def _mock_akshare_stock_individual_info_em(symbol="000001"):
    """ak.stock_individual_info_em() → 模拟个股信息"""
    return pd.DataFrame({
        "item": ["市盈率-动态", "市净率", "总市值", "流通市值"],
        "value": ["15.5", "1.8", "500亿", "350亿"],
    })


def _mock_akshare_stock_fund_flow_individual(symbol="即时"):
    """ak.stock_fund_flow_individual() → 空 DataFrame，触发 _batch_fund_flow 默认值路径

    注：真实 API 在 py_mini_racer 上 Python 3.11 wheel 触发 native crash
    (Windows 0x80000003 / macOS Trace/BPT trap)，必须 mock 阻断。
    """
    return pd.DataFrame(columns=["代码", "主力净流入-净额", "主力净流入-净占比", "成交额"])


def _mock_akshare_stock_individual_fund_flow(stock="000001", market="sh"):
    """ak.stock_individual_fund_flow() → 空 DataFrame，触发默认 0 流入"""
    return pd.DataFrame(columns=["日期", "主力净流入-净额", "主力净流入-净占比"])


def _mock_akshare_stock_zt_pool_em(date=""):
    """ak.stock_zt_pool_em() → 空涨停池 DataFrame，触发"无候选股"分支"""
    return pd.DataFrame(columns=["代码", "名称", "涨跌幅", "封板资金"])


def _mock_akshare_stock_lhb_detail_em(*args, **kwargs):
    """ak.stock_lhb_detail_em() → 空龙虎榜 DataFrame"""
    return pd.DataFrame(columns=["代码", "名称", "上榜日", "买入金额", "卖出金额"])


@pytest.fixture(autouse=True)
def _mock_akshare_by_default(request, monkeypatch):
    """默认 mock akshare 慢 API（避免 35+ min hang）

    豁免：标记 @pytest.mark.enable_socket 的测试可访问真实网络
    """
    if request.node.get_closest_marker("enable_socket"):
        yield
        return

    # monkeypatch 关键慢 API
    try:
        import akshare as ak
        monkeypatch.setattr(
            "akshare.stock_zh_a_spot", _mock_akshare_stock_zh_a_spot
        )
        monkeypatch.setattr(
            "akshare.stock_zh_index_daily", _mock_akshare_stock_zh_index_daily
        )
        monkeypatch.setattr(
            "akshare.stock_individual_info_em",
            _mock_akshare_stock_individual_info_em
        )
        # S34 扩展：阻断 py_mini_racer V8 native crash 路径 (Python 3.11 wheel)
        monkeypatch.setattr(
            "akshare.stock_fund_flow_individual",
            _mock_akshare_stock_fund_flow_individual
        )
        monkeypatch.setattr(
            "akshare.stock_individual_fund_flow",
            _mock_akshare_stock_individual_fund_flow
        )
        monkeypatch.setattr(
            "akshare.stock_zt_pool_em", _mock_akshare_stock_zt_pool_em
        )
        monkeypatch.setattr(
            "akshare.stock_lhb_detail_em", _mock_akshare_stock_lhb_detail_em
        )
    except ImportError:
        pass  # akshare 未装，不 mock

    yield


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
