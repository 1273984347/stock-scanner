"""P-108 判据（2026-11-20）：数据层本地优先、在线回填、离线只读。

用户要求：所有数据项目内都要有，不能只依赖在线源。
本文件钉住三条行为：命中本地→不碰网络；本地过期→在线取数并回填；离线→只读本地。
"""
import pandas as pd
import pytest

pytestmark = pytest.mark.local_store

import lib.data.data_source_registry as reg
import lib.data.local_store as ls


@pytest.fixture(autouse=True)
def _enabled_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "STORE_ROOT", tmp_path / "store")
    monkeypatch.setattr(ls, "MANIFEST_PATH", tmp_path / "store" / "manifest.json")
    monkeypatch.delenv("QUANT_LOCAL_STORE", raising=False)
    monkeypatch.delenv("QUANT_OFFLINE", raising=False)
    yield


def _hist(last=None, n=80):
    # 动态取最近交易日：写死日期会随真实时间推进被判「过期」，触发在线调用而假红。
    last = last or ls.last_trading_day()
    dates = pd.bdate_range(end=last, periods=n)
    return pd.DataFrame({
        "date": dates, "open": 10.0, "high": 11.0, "low": 9.0,
        "close": 10.5, "volume": 1e6,
    })


def _no_network(*a, **k):
    raise AssertionError("本地数据新鲜时不应访问在线源")


def test_fresh_local_hit_skips_network(monkeypatch):
    ls.write_frame("kline_raw", "600519", _hist())
    monkeypatch.setattr(reg, "_get_stock_hist_multi_source_online", _no_network)

    out = reg.get_stock_hist_multi_source("600519", days=60)
    assert len(out) == 60 and out["close"].iloc[-1] == 10.5


def test_stale_local_falls_back_and_writes_back(monkeypatch):
    fresh_day = ls.last_trading_day()
    stale_day = pd.bdate_range(end=fresh_day, periods=6)[0].date()  # 早 5 个工作日 ⇒ 过期
    ls.write_frame("kline_raw", "600519", _hist(last=stale_day))
    fresh = _hist(last=fresh_day, n=100)
    calls = []

    def online(*a, **k):
        calls.append(1)
        return fresh.copy()

    monkeypatch.setattr(reg, "_get_stock_hist_multi_source_online", online)
    out = reg.get_stock_hist_multi_source("600519", days=90)

    # 在线实现返回多少就返回多少（不改既有语义），但必须已回填
    assert len(out) == 100 and calls == [1]
    stored = ls.read_frame("kline_raw", "600519")
    assert pd.to_datetime(stored["date"]).max().date() == fresh_day


def test_adjusted_and_raw_are_separate_datasets(monkeypatch):
    ls.write_frame("kline", "600519", _hist().assign(close=99.0))       # 前复权
    ls.write_frame("kline_raw", "600519", _hist())                       # 不复权
    monkeypatch.setattr(reg, "_get_stock_hist_multi_source_online", _no_network)

    adj = reg.get_stock_hist_multi_source("600519", days=30, prefer_adjusted=True)
    raw = reg.get_stock_hist_multi_source("600519", days=30, prefer_adjusted=False)
    assert adj["close"].iloc[-1] == 99.0
    assert raw["close"].iloc[-1] == 10.5


def test_offline_without_local_raises_without_network(monkeypatch):
    monkeypatch.setenv("QUANT_OFFLINE", "1")
    monkeypatch.setattr(reg, "_get_stock_hist_multi_source_online", _no_network)

    with pytest.raises(reg.AllDataSourcesFailedError) as ei:
        reg.get_stock_hist_multi_source("300750", days=60)
    assert "offline" in str(ei.value)


def test_offline_with_local_returns_data(monkeypatch):
    ls.write_frame("kline_raw", "300750", _hist())
    monkeypatch.setenv("QUANT_OFFLINE", "1")
    monkeypatch.setattr(reg, "_get_stock_hist_multi_source_online", _no_network)

    out = reg.get_stock_hist_multi_source("300750", days=40)
    assert len(out) == 40


def test_explicit_source_or_as_of_date_bypasses_store(monkeypatch):
    """显式源 / 历史快照必须走在线链路，不能被「最新本地数据」替换。"""
    ls.write_frame("kline_raw", "600519", _hist())

    def online(*a, **k):
        return _hist(n=10)

    monkeypatch.setattr(reg, "_get_stock_hist_multi_source_online", online)
    assert len(reg.get_stock_hist_multi_source("600519", days=60, source="baostock")) == 10
    assert len(reg.get_stock_hist_multi_source("600519", days=60, as_of_date="2026-05-30")) == 10
