"""跨源成交量单位归一判据（P1-9，批次 C5，2026-11-20）

口径事实（复核 B3 + C3 westock 实测）：
- **手**：tushare pro.daily ``vol`` / akshare push2his ``成交量`` / 腾讯 fqkline /
  westock（后两者已在各自适配器/分支 ×100）
- **股**：新浪 K 线 / baostock / pytdx

归一原则：各源**就地** ×100（registry rename 分支内）；共享 ``_normalize_columns``
只做列名映射、绝不改数值——否则新浪/腾讯/baostock（已是股）会被重复 ×100
制造新事故。本文件按分支逐个钉住端到端行为 + 「股口径源不被双乘」守卫。

patch 口径（I4/G2）：各源函数在分支内延迟 import → 一律 patch 定义方模块属性；
熔断器全量 reset 后再测（进程级状态，conftest 不按用例清零）。
"""

import pandas as pd
import pytest

from lib.data import data_baostock, data_kline_web, data_source_registry as reg
from lib.data import data_tushare
from lib.data.data_source_registry import (
    _fetch_from_source,
    _normalize_columns,
    reset_circuit_breakers,
)


def _ohlcv(volume: float, date: str = "2026-11-19") -> pd.DataFrame:
    return pd.DataFrame([{
        "date": date, "open": 1442.0, "high": 1464.99,
        "low": 1432.10, "close": 1460.21, "volume": volume,
    }])


def test_tushare_volume_scaled_from_lots(monkeypatch):
    """tushare vol=手 → registry 分支 ×100 归一到股（复核 B3）。"""
    reset_circuit_breakers()
    raw = pd.DataFrame([{
        "trade_date": "20261119", "open": 1442.0, "close": 1460.21,
        "high": 1464.99, "low": 1432.10, "vol": 42692,  # 手
    }])
    monkeypatch.setattr(data_tushare, "get_daily", lambda *a, **k: raw.copy())
    df = _fetch_from_source("600519", 30, "tushare")
    assert not df.empty
    assert float(df["volume"].iloc[0]) == pytest.approx(4269200.0)


def test_akshare_volume_scaled_from_lots(monkeypatch):
    """akshare 成交量（东财 push2his 口径）= 手 → ×100。"""
    import akshare

    reset_circuit_breakers()
    raw = pd.DataFrame([{
        "日期": "2026-11-19", "开盘": 1442.0, "收盘": 1460.21,
        "最高": 1464.99, "最低": 1432.10, "成交量": 42692,  # 手
        "成交额": 6192210000, "换手率": 0.34, "涨跌幅": 1.24,
    }])
    monkeypatch.setattr(akshare, "stock_zh_a_hist", lambda *a, **k: raw.copy())
    # 2026-11-22 Δ5 规约变更（P-125/T7-e）：akshare 声明为 qfq-only 能力
    # （分支固定 adjust="qfq"），raw 语义被能力守卫拒绝——本测试走 qfq 意图。
    df = _fetch_from_source("600519", 30, "akshare", prefer_adjusted=True)
    assert not df.empty
    assert float(df["volume"].iloc[0]) == pytest.approx(4269200.0)


@pytest.mark.parametrize("source,adapter", [
    ("sina_kline", "get_sina_kline"),
    ("tencent_kline", "get_tencent_kline"),
])
def test_share_denominated_sources_not_double_scaled(monkeypatch, source, adapter):
    """新浪/腾讯适配器已输出股——registry 分支必须原样透传（防未来双乘）。"""
    reset_circuit_breakers()
    monkeypatch.setattr(data_kline_web, adapter,
                        lambda *a, **k: _ohlcv(4269153.0))
    df = _fetch_from_source("600519", 30, source)
    assert not df.empty
    assert float(df["volume"].iloc[0]) == pytest.approx(4269153.0)


def test_baostock_volume_passthrough_not_scaled(monkeypatch):
    """baostock 口径为股 → registry baostock 分支透传。"""
    reset_circuit_breakers()
    monkeypatch.setattr(data_baostock, "get_kline",
                        lambda *a, **k: _ohlcv(4269153.0))
    df = _fetch_from_source("600519", 30, "baostock")
    assert not df.empty
    assert float(df["volume"].iloc[0]) == pytest.approx(4269153.0)


def test_westock_volume_board_aware_scaling(monkeypatch):
    """P-122（ADR-0036 D1）：westock 报文单位与腾讯同规则——主板=手 ×100，
    科创板 688/689 已是股 ×1（2026-11-21 实测 688001 原始=10276186 与 Wind 同值，
    一刀切 ×100 曾让 610 只科创板 volume 虚增 100 倍）。"""
    import lib.data.data_westock as dw

    reset_circuit_breakers()
    monkeypatch.setattr(dw, "get_kline", lambda *a, **k: _ohlcv(42692.0))
    df = _fetch_from_source("600519", 30, "westock")
    assert not df.empty
    assert float(df["volume"].iloc[0]) == pytest.approx(4269200.0)  # 手 → 股

    reset_circuit_breakers()
    monkeypatch.setattr(dw, "get_kline", lambda *a, **k: _ohlcv(10276186.0))
    df = _fetch_from_source("688001", 30, "westock")
    assert not df.empty
    assert float(df["volume"].iloc[0]) == pytest.approx(10276186.0)  # 科创板已是股


def test_normalize_columns_never_scales_volume():
    """设计守卫：共享归一层只改列名不改数值（双乘防线，见 westock/tushare 注释）。"""
    raw = pd.DataFrame([{
        "日期": "2026-11-19", "开盘": 1442.0, "收盘": 1460.21,
        "最高": 1464.99, "最低": 1432.10, "成交量": 42692,
    }])
    out = _normalize_columns(raw)
    assert "volume" in out.columns  # 列名已映射
    assert float(out["volume"].iloc[0]) == pytest.approx(42692.0)  # 数值原样
