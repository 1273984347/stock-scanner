"""P-108 机器判据（2026-11-20）：项目内本地数据仓的基本契约。

用户要求：所有数据项目内都要有，不能只依赖在线源。
`cache/` 是 TTL 缓存（过期即删），不能承担这个职责；本仓独立于 `data/store/`。
"""
import json
from datetime import datetime

import pandas as pd
import pytest

pytestmark = pytest.mark.local_store

import lib.data.local_store as ls


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "STORE_ROOT", tmp_path / "store")
    monkeypatch.setattr(ls, "MANIFEST_PATH", tmp_path / "store" / "manifest.json")
    monkeypatch.delenv("QUANT_LOCAL_STORE", raising=False)
    monkeypatch.delenv("QUANT_OFFLINE", raising=False)
    yield


def _df(last="2026-11-19"):
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-11-18", last]),
        "open": [10.0, 11.0], "high": [11.0, 12.0],
        "low": [9.0, 10.0], "close": [10.5, 11.5], "volume": [1e6, 2e6],
    })


def test_frame_roundtrip_and_manifest():
    assert ls.write_frame("kline", "600519", _df())
    out = ls.read_frame("kline", "600519")
    assert len(out) == 2 and out["close"].iloc[-1] == 11.5

    cov = ls.coverage()
    assert cov["kline"]["files"] == 1 and cov["kline"]["rows"] == 2
    assert json.loads(ls.MANIFEST_PATH.read_text(encoding="utf-8"))["schema_version"] == 1


def test_write_frame_sorts_by_date():
    """P-123 收口（ADR-0036 D2）：降序帧落仓即转升序，tail() 语义由此成立。"""
    desc = _df().iloc[::-1].reset_index(drop=True)
    assert ls.write_frame("kline", "600519", desc)
    out = ls.read_frame("kline", "600519")
    assert list(out["date"]) == list(pd.to_datetime(["2026-11-18", "2026-11-19"]))


def test_write_frame_rejects_schema_outside_whitelist():
    """P-124 收口（ADR-0036 D3）：白名单外列集拒写，厂商原始帧不得直落仓。"""
    bad = pd.DataFrame({
        "stock_code": ["600519"], "trade_time": pd.to_datetime(["2026-11-19"]),
        "date": pd.to_datetime(["2026-11-19"]), "open": [10.0], "close": [10.5],
        "high": [11.0], "low": [9.0], "volume": [1e6], "amount": [1.05e7],
        "change_pct": [0.5], "change": [0.05], "turnover_ratio": [1.0],
        "pre_close": [10.45],
    })
    assert ls.write_frame("kline", "600519", bad) is False
    assert ls.read_frame("kline", "600519").empty


def test_write_frame_unregistered_dataset_not_blocked():
    """未登记数据集（snapshot）不校验白名单——登记一个收敛一个（ADR-0036 D3）。"""
    snap = pd.DataFrame({"code": ["600519"], "price": [10.5]})
    assert ls.write_frame("snapshot", "2026-11-21", snap)


def test_empty_frame_never_written():
    assert ls.write_frame("kline", "000001", pd.DataFrame()) is False
    assert ls.read_frame("kline", "000001").empty


def test_json_roundtrip():
    assert ls.write_json("financial", "600519", {"roe": 0.34})
    assert ls.read_json("financial", "600519")["roe"] == 0.34


def test_switch_off_disables_everything(monkeypatch):
    assert ls.write_frame("kline", "600519", _df())
    monkeypatch.setenv("QUANT_LOCAL_STORE", "off")
    assert ls.enabled() is False
    assert ls.read_frame("kline", "600519").empty
    assert ls.write_frame("kline", "600519", _df()) is False


def test_freshness_semantics():
    today = datetime(2026, 11, 20, 6, 30)     # 盘前，最近交易日 = 11-19
    assert ls.last_trading_day(today).isoformat() == "2026-11-19"

    ls.write_frame("kline", "600519", _df("2026-11-19"))
    assert ls.is_fresh("kline", "600519", today) is True

    ls.write_frame("kline", "600519", _df("2026-11-18"))
    assert ls.is_fresh("kline", "600519", today) is False, "落后一个交易日必须判不新鲜"

    ls.write_frame("snapshot", "2026-11-19", _df())
    assert ls.is_fresh("snapshot", "2026-11-19", today) is True
    assert ls.is_fresh("snapshot", "2026-11-18", today) is False


def test_offline_flag_is_independent_of_enabled():
    """离线 != 禁用：离线仍读本地，只是禁止在线请求。"""
    import os
    os.environ["QUANT_OFFLINE"] = "1"
    try:
        assert ls.enabled() is True and ls.offline() is True
    finally:
        os.environ.pop("QUANT_OFFLINE", None)


def test_touch_files_counted_once_on_rewrite():
    """C15：重写同一文件不得重复计 files（账实漂移的结构性根源之一）。"""
    assert ls.write_frame("kline", "600519", _df())
    assert ls.write_frame("kline", "600519", _df())  # 重写
    assert ls.write_frame("kline", "000001", _df())
    cov = ls.coverage()
    assert cov["kline"]["files"] == 2, "重写 +1 ⇒ files 变累计写次数"


def test_rebuild_manifest_matches_disk_truth():
    """C15 对账器：rebuild 后 manifest 原始账与盘上真值一致；dry_run 不写盘。

    注意 coverage() 的 files 本来就从盘上现算（rows 取 manifest）——
    账实漂移只藏在 manifest 原始 json 里（files=累计写次数 / rows=累计行数）。
    """
    ls.write_frame("kline", "600519", _df())
    ls.write_frame("kline", "000001", _df())
    ls.write_json("financial", "600519", {"roe": 0.34})
    ls.write_json("fundflow", "600519", {"main_net_ratio": 0.03,
                                         "net_inflow_5d": 0.0})
    # 人为制造账实漂移（模拟旧版累计计数）
    m = json.loads(ls.MANIFEST_PATH.read_text(encoding="utf-8"))
    m["datasets"]["kline"]["files"] = 99
    m["datasets"]["kline"]["rows"] = 999
    ls.MANIFEST_PATH.write_text(json.dumps(m), encoding="utf-8")

    dry = ls.rebuild_manifest(dry_run=True)
    assert dry["kline"]["files"] == 2 and dry["kline"]["rows"] == 4
    assert json.loads(ls.MANIFEST_PATH.read_text(encoding="utf-8"))[
        "datasets"]["kline"]["files"] == 99, "dry_run 不得写盘"

    rebuilt = ls.rebuild_manifest()
    assert rebuilt["kline"]["files"] == 2 and rebuilt["kline"]["rows"] == 4
    assert rebuilt["financial"]["files"] == 1 and rebuilt["financial"]["rows"] == 1
    after = json.loads(ls.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert after["datasets"]["kline"]["files"] == 2
    assert after["datasets"]["kline"]["rows"] == 4
    assert ls.coverage()["kline"]["rows"] == 4
