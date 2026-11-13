"""get_stock_fund_flow 缓存去重行为（H1 门禁）

背景：扫描时 5 个策略 + ML 富集循环会对同一只股票重复调用
get_stock_fund_flow，而原函数无任何缓存，被全局 3/sec 限流放大成 5+ 分钟瓶颈。

本测试把「缓存命中 / 去重」逻辑变成机器判据（与 RF-01 基线 pin 机制同构）：
- 进程内字典缓存：同 code 重复调用只触发 1 次底层 fetch（去重，解除瓶颈）
- 磁盘缓存：底层 fetch 失败/被替换时，从磁盘缓存复用（跨扫描复用）
- 仅缓存成功取数（非全零），不缓存「所有源失败」的默认值，避免掩盖瞬时故障

全程使用 hermetic fake，不触网、不落真盘。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest

import lib.data.data_layer as data_layer


@pytest.fixture(autouse=True)
def _reset_fundflow_cache():
    """每个用例前后清空进程内缓存，避免跨用例污染。"""
    data_layer._fundflow_cache.clear()
    yield
    data_layer._fundflow_cache.clear()


def _install_fakes(monkeypatch, disk_store, value_fn=None):
    """安装 hermetic fakes：fetch 计数 + 磁盘缓存 in-memory。

    value_fn(code) -> dict 控制每次 fetch 的返回值；缺省对所有 code 返回同一固定值。
    """
    calls = {"n": 0}

    def fake_fetch(code):
        calls["n"] += 1
        if value_fn is not None:
            return value_fn(code)
        return {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}

    def fake_load(key, max_age_hours=2):
        df = disk_store.get(key)
        return df if df is not None else pd.DataFrame()

    def fake_save(key, df):
        disk_store[key] = df

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", fake_fetch)
    monkeypatch.setattr(data_layer, "_load_cache", fake_load)
    monkeypatch.setattr(data_layer, "_save_cache", fake_save)
    return calls


def test_in_process_dedup_only_one_fetch(monkeypatch):
    """同一 code 重复调用只触发 1 次底层 fetch（去重，解除 5+ 分钟瓶颈）。"""
    disk_store = {}
    calls = _install_fakes(monkeypatch, disk_store)
    code = "600519"
    expected = {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}

    r1 = data_layer.get_stock_fund_flow(code)
    r2 = data_layer.get_stock_fund_flow(code)
    r3 = data_layer.get_stock_fund_flow(code)

    assert calls["n"] == 1, f"预期只 fetch 1 次，实际 {calls['n']} 次"
    assert r1 == r2 == r3 == expected
    # 进程内缓存命中（第二次起不再进 fetch）
    assert data_layer._fundflow_cache.get(code) is not None


def test_distinct_codes_each_fetched_once(monkeypatch):
    """不同 code 各自只 fetch 一次，互不串缓存。"""
    disk_store = {}

    def per_code(code):
        # 用 code 派生确定且互异的值，便于断言「不同 code 互不复用缓存」
        h = int(code[-2:]) if code[-2:].isdigit() else 0
        return {"main_net_ratio": round(0.01 * (h % 10), 4), "net_inflow_5d": float(h) * 1e6}

    calls = _install_fakes(monkeypatch, disk_store, value_fn=per_code)
    a = data_layer.get_stock_fund_flow("600519")
    b = data_layer.get_stock_fund_flow("000001")
    a2 = data_layer.get_stock_fund_flow("600519")

    assert calls["n"] == 2, f"两只不同股票应 fetch 2 次，实际 {calls['n']} 次"
    assert a == a2
    assert a != b


def test_disk_cache_reused_when_fetch_fails(monkeypatch):
    """底层 fetch 抛异常时，磁盘缓存仍能被复用（跨扫描复用）。"""
    disk_store = {}
    code = "000001"
    good = {"main_net_ratio": 0.03, "net_inflow_5d": 5.0e7}
    disk_store[f"fundflow/{code}"] = pd.DataFrame([good])

    def boom(_code):
        raise RuntimeError("network down")

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", boom)
    monkeypatch.setattr(data_layer, "_load_cache",
                        lambda key, max_age_hours=2: disk_store.get(key, pd.DataFrame()))
    monkeypatch.setattr(data_layer, "_save_cache", lambda key, df: None)

    res = data_layer.get_stock_fund_flow(code)
    assert res == good, f"磁盘缓存应被复用，实际返回 {res}"


def test_only_nonzero_results_cached(monkeypatch):
    """全零结果（所有源失败）不写入缓存，便于后续重试。"""
    disk_store = {}
    data_layer._fundflow_cache.clear()

    def zero(_code):
        return {"main_net_ratio": 0, "net_inflow_5d": 0}

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", zero)
    monkeypatch.setattr(data_layer, "_load_cache", lambda key, max_age_hours=2: pd.DataFrame())
    monkeypatch.setattr(data_layer, "_save_cache",
                        lambda key, df: disk_store.__setitem__(key, df))

    res = data_layer.get_stock_fund_flow("600000")
    assert res == {"main_net_ratio": 0, "net_inflow_5d": 0}
    # 不应写入磁盘缓存
    assert "fundflow/600000" not in disk_store
    # 进程内也不应缓存（下次再试仍会 fetch）
    assert data_layer._fundflow_cache.get("600000") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
