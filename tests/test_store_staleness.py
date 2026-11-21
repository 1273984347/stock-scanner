"""Δ2 哨兵判据（2026-11-22）：本地仓整仓新鲜度告警面 + 晨报接线。

评审依据：docs/audit/architecture-review-delta-2026-11-22.md Δ2——store 陈旧
此前无任何运行时信号（/api/health 只看 cache、coverage() 无运行时消费方）。
修复 = local_store.staleness_report()（只读记账，不触发取数）+ 晨报前置告警段。
口径 = manifest.updated_at 距今 >3 天即 stale（衡量「同步管道是否在跑」）。
"""
import inspect
from datetime import datetime

import pytest

pytestmark = pytest.mark.local_store

import lib.data.local_store as ls
from reporter import scheduler


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "STORE_ROOT", tmp_path / "store")
    monkeypatch.setattr(ls, "MANIFEST_PATH", tmp_path / "store" / "manifest.json")
    monkeypatch.delenv("QUANT_LOCAL_STORE", raising=False)
    monkeypatch.delenv("QUANT_OFFLINE", raising=False)
    yield


def _write_recent(dataset, key="600519"):
    import pandas as pd
    ls.write_frame(dataset, key, pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        "open": [1.0, 1.0], "high": [1.0, 1.0],
        "low": [1.0, 1.0], "close": [1.0, 1.0], "volume": [1e6, 1e6],
    }))


def test_fresh_dataset_reports_ok():
    _write_recent("kline")
    rows = {ds: (st, det) for ds, st, det in ls.staleness_report()}
    assert rows["kline"][0] == "ok"


def test_stale_dataset_detected_by_virtual_clock():
    """记账时间在 3 天之前（now 推到未来 10 天）必须判 stale 并带天数。"""
    from datetime import timedelta
    _write_recent("kline")
    future = datetime.now() + timedelta(days=10)
    rows = {ds: (st, det) for ds, st, det in ls.staleness_report(now=future)}
    assert rows["kline"][0] == "stale"
    assert "天未同步" in rows["kline"][1]


def test_empty_and_missing_datasets_reported():
    rows = {ds: (st, det) for ds, st, det in ls.staleness_report()}
    assert rows["kline"][0] == "empty"
    assert rows["kline_raw"][0] == "empty"


def test_disabled_store_reports_disabled(monkeypatch):
    monkeypatch.setenv("QUANT_LOCAL_STORE", "off")
    rows = ls.staleness_report()
    assert rows == [("local_store", "disabled", "QUANT_LOCAL_STORE=off")]


def test_scheduler_morning_wires_store_staleness():
    """晨报入口必须接本地仓陈旧告警（Δ2 哨兵的每日触点）。"""
    src = inspect.getsource(scheduler.send_morning_report)
    assert "staleness_report" in src
    assert "本地仓陈旧告警" in src
