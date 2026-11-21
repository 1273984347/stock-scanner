"""C15（2026-11-21）机器判据：data_sync 数据集补齐的接线判据。

覆盖三件事：
1. ``sync_kline(dataset=...)`` 把复权口径正确传给取数层（kline→True / kline_raw→False），
   且单源模式下写对应数据集（T7-g 分仓纪律：前复权与不复权绝不混写）；
2. ``sync_fundflow`` 批量层优先：命中码直接落仓、缺口才走逐股（ADR-0018 语义）；
3. 成功判据区分「真实零值」与「缺失」（P-114：None 才是缺失，合法零值算成功）。
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    from lib.data import local_store as ls
    monkeypatch.setattr(ls, "STORE_ROOT", tmp_path / "store")
    monkeypatch.setattr(ls, "MANIFEST_PATH", tmp_path / "store" / "manifest.json")
    monkeypatch.delenv("QUANT_LOCAL_STORE", raising=False)
    monkeypatch.delenv("QUANT_OFFLINE", raising=False)
    return ls


def _load_sync():
    spec = importlib.util.spec_from_file_location(
        "_data_sync", ROOT / "scripts" / "data_sync.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_hist(code, days=250, prefer_adjusted=False, source="auto", **kw):
    import pandas as pd
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=5),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
        "volume": 1000,
    })


def test_sync_kline_raw_passes_unadjusted_and_writes_raw(isolated_store, monkeypatch):
    """kline_raw 同步必须传 prefer_adjusted=False 且落 kline_raw 仓（分仓纪律）。"""
    import lib.data.data_source_registry as reg
    captured = {}

    def spy(code, days=250, prefer_adjusted=False, source="auto", **kw):
        captured["prefer_adjusted"] = prefer_adjusted
        captured["source"] = source
        return _fake_hist(code, days=days, prefer_adjusted=prefer_adjusted,
                          source=source, **kw)

    monkeypatch.setattr(reg, "get_stock_hist_multi_source", spy)
    mod = _load_sync()
    ok, fail, skipped = mod.sync_kline(["600519"], days=250, sleep=0,
                                       source="tencent_kline", dataset="kline_raw")
    assert (ok, fail, skipped) == (1, 0, 0)
    assert captured["prefer_adjusted"] is False
    assert isolated_store.read_frame("kline_raw", "600519").shape[0] == 5
    assert isolated_store.read_frame("kline", "600519").empty, \
        "kline_raw 同步不得写前复权仓（分仓纪律 T7-g）"


def test_sync_kline_keeps_adjusted(isolated_store, monkeypatch):
    """kline 同步保持 prefer_adjusted=True（回测栈口径不回归）。"""
    import lib.data.data_source_registry as reg
    captured = {}

    def spy(code, days=250, prefer_adjusted=False, source="auto", **kw):
        captured["prefer_adjusted"] = prefer_adjusted
        return _fake_hist(code, days=days, prefer_adjusted=prefer_adjusted,
                          source=source, **kw)

    monkeypatch.setattr(reg, "get_stock_hist_multi_source", spy)
    mod = _load_sync()
    ok, fail, _ = mod.sync_kline(["600519"], days=250, sleep=0,
                                 source="tencent_kline", dataset="kline")
    assert (ok, fail) == (1, 0)
    assert captured["prefer_adjusted"] is True


def test_sync_fundflow_batch_first_and_zero_is_success(isolated_store, monkeypatch):
    """批量层命中的码直接落仓不逐股；合法零值算成功（None 才是缺失，P-114）。"""
    import lib.data.data_layer as dl
    batch_seen = []
    perstock_seen = []

    monkeypatch.setattr(dl, "_get_market_fund_flow",
                        lambda: {"600519": {"main_net_ratio": 0.03,
                                            "net_inflow_5d": 1.2e8},
                                 "000001": {"main_net_ratio": 0.0,
                                            "net_inflow_5d": 0.0}})

    def fake_perstock(code):
        batch_seen.append("called")
        perstock_seen.append(code)
        return {"main_net_ratio": None, "net_inflow_5d": None}  # 模拟缺失

    monkeypatch.setattr(dl, "get_stock_fund_flow", fake_perstock)
    mod = _load_sync()
    ok, fail, skipped = mod.sync_fundflow(["600519", "000001", "300750"],
                                          sleep=0, use_batch=True)
    # 600519/000001 批量命中（零值算成功）；300750 缺口走逐股 → 缺失判 fail
    assert ok == 2
    assert fail == 1
    assert perstock_seen == ["300750"], "批量命中的码不得再逐股拉取"
    assert isolated_store.read_json("fundflow", "600519")["main_net_ratio"] == 0.03
    assert isolated_store.read_json("fundflow", "000001") == {
        "main_net_ratio": 0.0, "net_inflow_5d": 0.0}
    assert isolated_store.read_json("fundflow", "300750") is None
