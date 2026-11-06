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


# ============================================================================
# 默认禁网（Phase 0 慢用例治理，2026-11-07）
#
# 背景：上面那个 akshare mock 只挡了 7 个接口 —— sina / eastmoney /
#       其余 akshare 接口 / requests 直连 仍然漏网。实测本地全量 pytest
#       635.37s（10.6min），其中 tests/test_scorecard.py 单文件 304.65s
#       （占 47.9%）—— 根因正是这些未隔离的真实网络路径。
#       详见 docs/refactor/test-duration-baseline.md。
#
# 修法：autouse fixture 默认阻断 socket 建连与 DNS 解析；
#       标记 @pytest.mark.enable_socket 的测试显式豁免。
#
# 关键设计：抛 ConnectionError 子类而非 RuntimeError ——
#       requests / urllib3 会把 socket 的 OSError 包装成自己的
#       ConnectionError。抛同族异常才能让下游「网络失败降级」分支
#       与真实断网时保持一致，不会因异常类型变了而改变行为。
# ============================================================================


class NetworkBlockedError(ConnectionError):
    """测试期间的网络访问被默认阻断（用 @pytest.mark.enable_socket 豁免）"""


# 记录被拦截的调用，便于失败时诊断（fixture teardown 时由调用方读取）
_BLOCKED_NETWORK_CALLS = []

# 原始实现，供"放行本机地址"时回退调用
_REAL_CONNECT = socket.socket.connect
_REAL_CREATE_CONNECTION = socket.create_connection


# 放行的地址：loopback（127.0.0.1 / ::1 / localhost）与 unix socket path
#   —— 这些不是"外部网络"，且必须放行，否则会打断基础设施：
#   · Windows 上 asyncio 的 self-pipe 用 socket.socketpair()，而它在 Windows
#     是用真实 connect() 到 127.0.0.1 模拟实现的 ⇒ 挡掉会导致 event loop
#     创建失败（'ProactorEventLoop' object has no attribute '_ssock'），
#     连带所有用 FastAPI TestClient 的测试全挂。
#   · Linux/macOS 上 multiprocessing 的 socketpair 走 AF_UNIX（str path）。
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}
_REAL_GETADDRINFO = socket.getaddrinfo


def _is_local(address) -> bool:
    """判定地址是否为本地/回环（非外部网络）。"""
    if isinstance(address, (str, bytes)):
        # unix socket path —— 非网络
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        if isinstance(host, bytes):
            host = host.decode("utf-8", "ignore")
        return str(host) in _LOOPBACK_HOSTS
    return False


@pytest.fixture(autouse=True)
def _zero_out_retry_backoff_by_default(monkeypatch):
    """默认把 API 重试退避压到 ~0（显式 patch 的测试不受影响）。

    背景：退避常量遗漏是本项目**反复出现**的慢用例来源 ——
      · lib/data_layer.py 的 API_RETRY_BASE_WAIT_S（模块常量）
      · lib/data_margin.py 的硬编码 time.sleep(2 ** attempt)
      · lib/data_baostock.py / lib/sentiment.py 的同类退避
    测试未短路 ⇒ 真等 2s / 4s。实测三处：
      test_datalayer_rate_limit  14.66s
      test_new_modules (margin)   6.65s
      test_strategies + test_batch_quick_score  33s

    为什么放 autouse 而不是逐个用例补：这类遗漏在**同一文件内也会漏**
    （同文件其它用例已正确 patch，唯独某条漏掉）⇒ 逐个补治标。
    默认零退避 + 显式 patch 优先，才是稳定口径。

    显式 patch 的测试（如专门测退避时长的 test_retry_call_*）不受影响：
    monkeypatch 后应用者覆盖先应用者（后入栈先撤销），测试内的 patch 优先生效。
    """
    monkeypatch.setattr("lib.data_layer.API_RETRY_BASE_WAIT_S", 0.01)
    # lib/data_layer.time 就是标准库 time 模块本身 ⇒ 这一处同时覆盖
    # data_layer / data_margin / data_baostock / sentiment 的全部退避 sleep
    monkeypatch.setattr("lib.data_layer.time.sleep", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _block_network_by_default(request, monkeypatch):
    """默认禁止真实网络访问；@pytest.mark.enable_socket 可豁免。"""
    if request.node.get_closest_marker("enable_socket"):
        yield
        return

    def _blocked_connect(self, address, *args, **kwargs):
        if _is_local(address):
            return _REAL_CONNECT(self, address, *args, **kwargs)
        _BLOCKED_NETWORK_CALLS.append(f"connect({address!r})")
        raise NetworkBlockedError(
            f"Network access blocked by default (address={address!r}). "
            "Mark this test with @pytest.mark.enable_socket to opt in."
        )

    def _blocked_create_connection(address, *args, **kwargs):
        if _is_local(address):
            return _REAL_CREATE_CONNECTION(address, *args, **kwargs)
        _BLOCKED_NETWORK_CALLS.append(f"create_connection({address!r})")
        raise NetworkBlockedError(
            f"Network access blocked by default (address={address!r}). "
            "Mark this test with @pytest.mark.enable_socket to opt in."
        )

    def _blocked_getaddrinfo(host, *args, **kwargs):
        if host is None or str(host) in _LOOPBACK_HOSTS:
            return _REAL_GETADDRINFO(host, *args, **kwargs)
        _BLOCKED_NETWORK_CALLS.append(f"getaddrinfo({host!r})")
        raise NetworkBlockedError(
            f"DNS resolution blocked by default (host={host!r}). "
            "Mark this test with @pytest.mark.enable_socket to opt in."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect)
    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)
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
