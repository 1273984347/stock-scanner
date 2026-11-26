"""blocks 刷新覆盖率判据（P-146）机器判据

背景：2026-11-26 的 blocks 刷新有 12 个行业板块失败，同步函数"成功多少写多少"，
750 个旧文件（13%）留在仓里，manifest 仍显示已最新。本组测试锁定三件事：
1. 部分映射（覆盖 <98%）**一个文件都不写**，并返回 fail=1；
2. 覆盖达标才写仓（边界 98% 精确可用）；
3. `fetch_industry_mapping` 必须把失败板块计数返回给调用方（error / empty 两种形态）。
"""
import importlib.util
import pathlib
import sys

import pandas as pd
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
    spec = importlib.util.spec_from_file_location("_data_sync_blocks", ROOT / "scripts" / "data_sync.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_mapping(monkeypatch, mapping, total=84, failures=None):
    from lib.data import data_sector as ds
    result = ds.IndustryMappingResult(mapping, total, failures or [])
    monkeypatch.setattr(ds, "fetch_industry_mapping", lambda sleep=0: result)


# ---------- 1. 覆盖不足：整批不写 ----------
def test_partial_mapping_rejected_without_any_write(isolated_store, monkeypatch):
    mod = _load_sync()
    monkeypatch.setattr(mod, "_blocks_expected_count", lambda: (1000, "test"))
    _patch_mapping(monkeypatch,
                   {f"{i:06d}": ["行业A"] for i in range(900)},
                   failures=[{"label": "1", "name": "板块X", "reason": "empty"}])
    writes = []
    monkeypatch.setattr(isolated_store, "write_json",
                        lambda *a, **k: writes.append(a[:2]) or True)

    ok, fail, skip = mod.sync_blocks(0)
    assert (ok, fail, skip) == (0, 1, 0)
    assert writes == [], "覆盖不足时不得写任何文件"


def test_empty_mapping_returns_fail(isolated_store, monkeypatch):
    mod = _load_sync()
    _patch_mapping(monkeypatch, {}, total=0,
                   failures=[{"label": "", "name": "", "reason": "board_list empty"}])
    ok, fail, skip = mod.sync_blocks(0)
    assert (ok, fail, skip) == (0, 1, 0)


# ---------- 2. 覆盖率边界：98% ----------
@pytest.mark.parametrize("n,accepted", [(979, False), (980, True)])
def test_coverage_boundary_98pct(isolated_store, monkeypatch, n, accepted):
    mod = _load_sync()
    monkeypatch.setattr(mod, "_blocks_expected_count", lambda: (1000, "test"))
    _patch_mapping(monkeypatch, {f"{i:06d}": ["行业A"] for i in range(n)})
    writes = []
    monkeypatch.setattr(isolated_store, "write_json",
                        lambda *a, **k: writes.append(a[:2]) or True)

    ok, fail, _ = mod.sync_blocks(0)
    if accepted:
        assert (ok, fail) == (n, 0)
        assert len(writes) == n
    else:
        assert (ok, fail) == (0, 1)
        assert writes == []


# ---------- 3. 覆盖达标：真实写仓 ----------
def test_full_mapping_writes_files(isolated_store, monkeypatch):
    mod = _load_sync()
    monkeypatch.setattr(mod, "_blocks_expected_count", lambda: (100, "test"))
    _patch_mapping(monkeypatch, {f"{i:06d}": ["行业A"] for i in range(100)})

    ok, fail, skip = mod.sync_blocks(0)
    assert (ok, fail, skip) == (100, 0, 0)
    files = list((isolated_store.STORE_ROOT / "blocks").glob("*.json"))
    assert len(files) == 100


# ---------- 4. 分母：max(上一版文件数, 快照标的数) ----------
def test_expected_count_uses_max_of_prev_and_snapshot(isolated_store, tmp_path):
    blocks = isolated_store.STORE_ROOT / "blocks"
    blocks.mkdir(parents=True)
    for i in range(10):
        (blocks / f"{i:06d}.json").write_text("[]", encoding="utf-8")
    snap = isolated_store.STORE_ROOT / "snapshot"
    snap.mkdir(parents=True)
    pd.DataFrame({"代码": [f"{i:06d}" for i in range(8)]}).to_parquet(
        snap / "2026-11-26.parquet", index=False)

    mod = _load_sync()
    expected, baseline = mod._blocks_expected_count()
    assert expected == 10
    assert baseline == "prev=10/snapshot=8"


def test_expected_count_floor_when_no_baseline(isolated_store):
    mod = _load_sync()
    expected, baseline = mod._blocks_expected_count()
    assert expected == mod.BLOCKS_EXPECTED_FLOOR
    assert baseline == "floor"


# ---------- 5. fetch 侧：失败板块必须可见 ----------
def test_fetch_industry_mapping_reports_error_and_empty(monkeypatch):
    from lib.data import data_sector as ds

    class FakeAk:
        @staticmethod
        def stock_sector_spot(indicator="行业"):
            return pd.DataFrame({"label": ["A", "B", "C"], "板块": ["板块A", "板块B", "板块C"]})

        @staticmethod
        def stock_sector_detail(sector):
            if sector == "A":
                return pd.DataFrame({"code": ["000001", "600519"]})
            if sector == "B":
                return pd.DataFrame()          # 空表：最容易静默丢成员的形态
            raise RuntimeError("boom")          # 抛异常

    monkeypatch.setitem(sys.modules, "akshare", FakeAk)
    res = ds.fetch_industry_mapping(sleep=0)

    assert res.sectors_total == 3
    assert res.ok_sectors == 1
    assert {f["reason"] for f in res.failures} == {"empty", "error: boom"}
    assert set(res.mapping) == {"000001", "600519"}


def test_fetch_industry_mapping_board_list_failure(monkeypatch):
    from lib.data import data_sector as ds

    class FakeAk:
        @staticmethod
        def stock_sector_spot(indicator="行业"):
            raise RuntimeError("net down")

    monkeypatch.setitem(sys.modules, "akshare", FakeAk)
    res = ds.fetch_industry_mapping(sleep=0)
    assert res.mapping == {}
    assert res.sectors_total == 0
    assert len(res.failures) == 1 and res.failures[0]["reason"].startswith("board_list")
