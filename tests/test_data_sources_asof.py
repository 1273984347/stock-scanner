"""数据源 as_of_date 离线契约测试（RF-01 Phase 2）

覆盖"回测不得取到 T 日之后的数据"这条契约，**全部离线**（不触网、不依赖本地 TDX 数据）：

1. registry 把 as_of_date 透传给每个数据源（此前 baostock/adata/westock 三源漏传）
2. data_adata 用 as_of_date 算 end（此前收了参数却仍用 datetime.now()）
3. data_westock 返回后按 as_of_date 截断（CLI 不支持按日期查询）
4. data_tdx 的截断发生在 tail(days) 之前（少取而非多取）
5. lib/data_api 统一适配器按 K 线契约返回
"""

import pandas as pd
import pytest

from lib.data_protocol import KLINE_COLUMNS


# ========== 1. registry 透传 ==========

@pytest.mark.parametrize("source,module,attr", [
    ("baostock", "lib.data_baostock", "get_kline"),
    ("adata", "lib.data_adata", "get_kline"),
    ("westock", "lib.data_westock", "get_kline"),
    ("tdx", "lib.data_tdx", "get_kline"),
])
def test_registry_passes_as_of_date(source, module, attr, monkeypatch):
    """registry 必须把 as_of_date 透传给每个源，否则回测取到 T 日数据（前视偏差）。"""
    import importlib

    import lib.data_source_registry as reg

    captured = {}

    def fake(*args, **kwargs):
        captured["as_of_date"] = kwargs.get("as_of_date")
        return pd.DataFrame()

    mod = importlib.import_module(module)
    monkeypatch.setattr(mod, attr, fake)

    reg._fetch_from_source("600519", 30, source, as_of_date="2026-01-20")
    assert captured.get("as_of_date") == "2026-01-20", (
        f"source={source} 未收到 as_of_date"
    )


def test_registry_without_as_of_date_still_works(monkeypatch):
    """不传 as_of_date = 实时模式，必须仍能正常取数（行为不变）。"""
    import importlib

    import lib.data_source_registry as reg

    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs.get("as_of_date"))
        return pd.DataFrame()

    monkeypatch.setattr(importlib.import_module("lib.data_baostock"), "get_kline", fake)
    reg._fetch_from_source("600519", 30, "baostock")
    assert calls == [None]


# ========== 2. data_adata 用 as_of_date ==========

def test_adata_uses_as_of_date_as_end():
    """data_adata 此前收了 as_of_date 却仍用 datetime.now() => 前视偏差。"""
    from lib.data_adata import _resolve_end_date

    end = _resolve_end_date("2026-01-20")
    assert end == pd.Timestamp("2026-01-20")
    assert (end - pd.Timedelta(days=30)) < end




def test_adata_without_as_of_date_uses_today():
    """不传 as_of_date => 实时模式（仍用今天），行为不变。"""
    from lib.data_adata import _resolve_end_date

    end = _resolve_end_date(None)
    assert end is not None
    assert end >= pd.Timestamp("2020-01-01")




# ========== 3. data_westock 返回后截断 ==========

def test_westock_truncates_to_as_of_date(monkeypatch):
    import lib.data_westock as w

    md = (
        "| 日期 | 开盘 | 收盘 |\n"
        "| --- | --- | --- |\n"
        "| 2026-01-19 | 1 | 2 |\n"
        "| 2026-01-20 | 2 | 3 |\n"
        "| 2026-01-21 | 3 | 4 |\n"
    )
    monkeypatch.setattr(w, "_run_westock", lambda args: md)

    df = w.get_kline("000001", as_of_date="2026-01-20")
    assert len(df) == 2
    assert df["日期"].tolist() == ["2026-01-19", "2026-01-20"]


def test_westock_without_as_of_date_keeps_all(monkeypatch):
    import lib.data_westock as w

    md = (
        "| 日期 | 开盘 | 收盘 |\n"
        "| --- | --- | --- |\n"
        "| 2026-01-19 | 1 | 2 |\n"
        "| 2026-01-21 | 3 | 4 |\n"
    )
    monkeypatch.setattr(w, "_run_westock", lambda args: md)

    df = w.get_kline("000001")
    assert len(df) == 2


# ========== 4. data_tdx 截断在 tail 之前 ==========

def test_tdx_truncate_before_tail():
    from lib.data_protocol import truncate_to_date

    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=50, freq="D"),
        "open": range(50), "high": range(50), "low": range(50),
        "close": range(50), "volume": range(50),
    })
    truncated = truncate_to_date(df, "2026-01-20").tail(10)
    assert len(truncated) == 10
    assert truncated["date"].max() == pd.Timestamp("2026-01-20")


# ========== 5. 统一适配器 ==========

def test_data_api_get_kline_normalizes(monkeypatch):
    import lib.data_api as api
    import lib.data_layer as dl

    raw = pd.DataFrame({
        "日期": ["2026-01-01"], "开盘": [1.0], "收盘": [2.0],
        "最高": [3.0], "最低": [0.5], "成交量": [100],
    })
    monkeypatch.setattr(dl, "get_stock_hist_multi_source", lambda *a, **k: raw)

    out = api.get_kline("600519")
    assert list(out.columns) == KLINE_COLUMNS
    assert len(out) == 1


def test_data_api_get_kline_empty(monkeypatch):
    import lib.data_api as api
    import lib.data_layer as dl

    monkeypatch.setattr(dl, "get_stock_hist_multi_source", lambda *a, **k: pd.DataFrame())
    out = api.get_kline("600519")
    assert out.empty
    assert list(out.columns) == KLINE_COLUMNS


def test_data_api_passes_as_of_date(monkeypatch):
    import lib.data_api as api
    import lib.data_layer as dl

    captured = {}

    def fake(code, days, mode, as_of_date=None):
        captured["as_of_date"] = as_of_date
        return pd.DataFrame()

    monkeypatch.setattr(dl, "get_stock_hist_multi_source", fake)
    api.get_kline("600519", as_of_date="2026-01-20")
    assert captured["as_of_date"] == "2026-01-20"


def test_data_api_clear_caches_reports_sources():
    import lib.data_api as api

    cleared = api.clear_caches()
    assert isinstance(cleared, list)
    assert all(isinstance(x, str) for x in cleared)
