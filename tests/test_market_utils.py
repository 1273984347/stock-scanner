"""test_market_utils.py — TDD tests for lib/market_utils.py

修复 stock-scanner 项目中 7+ 处的错误市场检测模式：
- detect_market: 北证 (4/8 开头) 正确归 bj，不再误归 sz
- get_tdx_path: 优先 lday_4 缺失则 fallback lday（兼容 bj 无前复权）
- read_tdx_day_closes: 高层函数，复用 tdx_batch._read_closes_only
"""
import os
import struct
from pathlib import Path

import pytest


# ============================================================
# detect_market: 股票代码 → 市场标识
# ============================================================

@pytest.mark.parametrize("code,expected", [
    # 沪市：主板 + 科创板 + B 股 + 基金/ETF
    ("600519", "sh"),   # 沪市主板（贵州茅台）
    ("688981", "sh"),   # 科创板
    ("900901", "sh"),   # 沪 B 股
    ("500001", "sh"),   # 沪市基金
    ("510500", "sh"),   # 500 ETF
    ("560000", "sh"),   # 基金
    # 深市：主板 + 创业板 + B 股
    ("000001", "sz"),   # 深市主板（平安银行）
    ("300750", "sz"),   # 创业板（宁德时代）
    ("200002", "sz"),   # 深 B 股
    # 北证（核心修复点）
    ("430001", "bj"),   # 北证主板
    ("830799", "bj"),   # 新三板
    ("836149", "bj"),   # 新三板
    ("920000", "bj"),   # 北证
    ("880001", "bj"),   # 8 开头（旧代码 _is_bj_stock 已支持，detect_market 也要支持）
    # unknown：债券 / 非法
    ("", "unknown"),
    ("123456", "unknown"),  # 1x 开头 → 债券
    ("111111", "unknown"),
    ("abc123", "unknown"),
    # strip + zfill
    ("  600519  ", "sh"),
    # 注意："519" zfill 后是 "000519" → 0 开头 → sz（见下面 test_detect_market_short_code_zfilled）
])
def test_detect_market(code, expected):
    from lib.market_utils import detect_market
    assert detect_market(code) == expected


# 上面的 "519" 用例逻辑不明确，单独再测
def test_detect_market_short_code_zfilled():
    """短代码 zfill 到 6 位后判断"""
    from lib.market_utils import detect_market
    # "519" → "000519" → 0 开头 → sz
    assert detect_market("519") == "sz"
    # "6" → "000006" → 0 开头 → sz（虽然真实股票不存在，但规则要一致）
    assert detect_market("6") == "sz"
    # "430017" → 4 开头 → bj
    assert detect_market("430017") == "bj"


# ============================================================
# get_tdx_path: 路径解析 + lday_4 → lday fallback
# ============================================================

def test_get_tdx_path_qfq_priority(tmp_path, monkeypatch):
    """lday_4 存在时优先返回"""
    (tmp_path / "sh" / "lday_4").mkdir(parents=True)
    (tmp_path / "sh" / "lday_4" / "sh600519.day").touch()
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import get_tdx_path
    p = get_tdx_path("600519", prefer_qfq=True)
    assert p is not None
    assert "lday_4" in str(p)
    assert p.name == "sh600519.day"


def test_get_tdx_path_fallback_to_lday(tmp_path, monkeypatch):
    """lday_4 缺失时 fallback 到 lday（核心修复点：bj 无前复权）"""
    # 只建 lday/，不建 lday_4/
    (tmp_path / "bj" / "lday").mkdir(parents=True)
    (tmp_path / "bj" / "lday" / "bj430001.day").touch()
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import get_tdx_path
    p = get_tdx_path("430001", prefer_qfq=True)
    assert p is not None
    # 用 Path.parts 跨平台比较
    assert p.parent.name == "lday"
    assert "lday_4" not in p.parts
    assert p.name == "bj430001.day"


def test_get_tdx_path_bj_stock_routes_to_bj(tmp_path, monkeypatch):
    """4 开头归 bj 目录（核心修复点：旧代码归 sz）"""
    # 在 bj/ 和 sz/ 都建文件，验证只走 bj/
    (tmp_path / "bj" / "lday_4").mkdir(parents=True)
    (tmp_path / "sz" / "lday_4").mkdir(parents=True)
    (tmp_path / "bj" / "lday_4" / "bj430001.day").touch()
    (tmp_path / "sz" / "lday_4" / "sz430001.day").touch()
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import get_tdx_path
    p = get_tdx_path("430001")
    assert p is not None
    # 用 Path.parts 跨平台比较
    assert "bj" in p.parts
    assert "sz" not in p.parts


def test_get_tdx_path_unknown_returns_none(tmp_path, monkeypatch):
    """unknown 代码（如债券）返回 None"""
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import get_tdx_path
    assert get_tdx_path("123456") is None  # 1x 债券
    assert get_tdx_path("") is None
    assert get_tdx_path("abc") is None


def test_get_tdx_path_no_file_returns_none(tmp_path, monkeypatch):
    """市场识别正确但文件不存在 → None"""
    (tmp_path / "sh" / "lday_4").mkdir(parents=True)
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import get_tdx_path
    assert get_tdx_path("600519") is None  # 目录存在但文件不存在


def test_get_tdx_path_prefer_qfq_false(tmp_path, monkeypatch):
    """prefer_qfq=False 时优先 lday"""
    (tmp_path / "sh" / "lday").mkdir(parents=True)
    (tmp_path / "sh" / "lday_4").mkdir(parents=True)
    (tmp_path / "sh" / "lday" / "sh600519.day").touch()
    (tmp_path / "sh" / "lday_4" / "sh600519.day").touch()
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import get_tdx_path
    p = get_tdx_path("600519", prefer_qfq=False)
    assert p is not None
    # 用 Path 跨平台比较，避免 Windows 反斜杠问题
    assert p.parent.name == "lday"
    assert "lday_4" not in p.parts


# ============================================================
# read_tdx_day_closes: 高层函数（封装 struct.unpack）
# ============================================================

def _make_day_file(path: Path, dates: list[int], close: int = 1000) -> None:
    """写一个含指定日期序列的 fake .day 文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        for d in dates:
            rec = struct.pack(
                "<IIIII fII",
                d,         # date
                close,     # open
                close + 10,  # high
                close - 10,  # low
                close,     # close
                1.0,       # amount
                100,       # volume
                0,         # reserved
            )
            f.write(rec)


def test_read_tdx_day_closes_empty_when_no_file(tmp_path, monkeypatch):
    """文件不存在 → None"""
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import read_tdx_day_closes
    assert read_tdx_day_closes("600519") is None


def test_read_tdx_day_closes_bj_fallback(tmp_path, monkeypatch):
    """bj 股票走 lday fallback（核心修复点）"""
    # _read_closes_only 要求 n >= max(days, 10)，需要至少 10 条记录
    dates = [20260520 + i for i in range(20)]
    _make_day_file(tmp_path / "bj" / "lday" / "bj430001.day", dates, close=5000)
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import read_tdx_day_closes
    closes = read_tdx_day_closes("430001", days=5)
    assert closes is not None
    # close 值应被除以 100: 5000/100 = 50.0
    assert all(abs(c - 50.0) < 0.01 for c in closes), f"got {closes}"


def test_read_tdx_day_closes_lday_4_priority(tmp_path, monkeypatch):
    """lday_4 存在时优先"""
    dates = [20260520 + i for i in range(20)]
    _make_day_file(tmp_path / "sh" / "lday_4" / "sh600519.day", dates, close=2000)
    _make_day_file(tmp_path / "sh" / "lday" / "sh600519.day", dates, close=9999)
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import read_tdx_day_closes
    closes = read_tdx_day_closes("600519", days=5)
    assert closes is not None
    # 读的是 lday_4 的 close = 2000/100 = 20.0
    assert all(abs(c - 20.0) < 0.01 for c in closes), f"got {closes}"


def test_read_tdx_day_ohlcv_basic(tmp_path, monkeypatch):
    """read_tdx_day_ohlcv 返回 DataFrame with OHLCV columns"""
    dates = [20260520 + i for i in range(20)]
    _make_day_file(tmp_path / "bj" / "lday" / "bj430001.day", dates, close=5000)
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import read_tdx_day_ohlcv
    df = read_tdx_day_ohlcv("430001", days=10)
    assert df is not None
    assert list(df.columns) == ["date", "open", "high", "low", "close", "amount", "volume"]
    assert len(df) == 10
    # close 字段已除以 100
    assert abs(df["close"].iloc[0] - 50.0) < 0.01


def test_read_tdx_day_ohlcv_bj_fallback(tmp_path, monkeypatch):
    """bj 股票无 lday_4 时 fallback 到 lday（核心修复点）"""
    dates = [20260520 + i for i in range(20)]
    _make_day_file(tmp_path / "bj" / "lday" / "bj430001.day", dates, close=5000)
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import read_tdx_day_ohlcv
    df = read_tdx_day_ohlcv("430001", days=5)
    # 旧代码: bj 错归 sz → sz/lday_4/sz430001.day 不存在 → None
    # 新代码: bj 走 bj/lday/bj430001.day → 5 行 DataFrame
    assert df is not None
    assert len(df) == 5


# ============================================================
# 集成测试
# ============================================================

@pytest.mark.parametrize("code,market", [
    ("600519", "sh"),
    ("000001", "sz"),
    ("430001", "bj"),
    ("830799", "bj"),
    ("510500", "sh"),
    ("300750", "sz"),
])
def test_market_routing_integration(tmp_path, monkeypatch, code, market):
    """集成：detect_market + get_tdx_path 联动"""
    (tmp_path / market / "lday").mkdir(parents=True)
    (tmp_path / market / "lday" / f"{market}{code}.day").touch()
    monkeypatch.setenv("TDX_DATA_PATH", str(tmp_path))

    from lib.market_utils import detect_market, get_tdx_path
    assert detect_market(code) == market
    assert get_tdx_path(code) is not None
