# tests/test_cycle_heat.py
from unittest.mock import patch

import pandas as pd
import pytest

import lib.cycle_heat as ch


@pytest.fixture(autouse=True)
def _clean_memo():
    """隔离各测试的板块分位进程 memo（防止 _ts/月粒度 key 串扰）"""
    ch._BLOCK_PCT_MEMO.clear()
    yield
    ch._BLOCK_PCT_MEMO.clear()


def _yoy_series(n=40, vals=None):
    # freq="QE" 需 pandas>=2.2（当前环境 2.3.3 满足）；<2.2 的旧环境请改用 "Q"
    dates = pd.date_range("2015-03-31", periods=n, freq="QE").strftime("%Y%m%d")
    v = vals if vals is not None else list(range(n))
    return pd.DataFrame({
        "date": list(dates),
        "inventory_yoy": v,
        "revenue_yoy": v,
        "capex_yoy": v,
        "debt_yoy": v,
    })


def test_hot_count_map_to_indicators_above_threshold():
    # 分位 0.9/0.9/0.2 对着 pctile_high=0.60 → hot_count=2（W003 校准后 exit 需 2）
    pct_map = {"inventory": 0.9, "capex": 0.9, "debt": 0.2}
    with patch.object(ch, "_stock_level_pct_map", return_value=pct_map):
        r = ch.get_block_cycle_heat("测试", None)
    assert r is not None
    assert r.hot_count == 2
    assert set(r.hot_indicators) == {"inventory_yoy", "capex_yoy"}
    assert r.inventory_pct == 0.9
    assert r.n_obs == 3


def test_no_hot_when_all_below_threshold():
    pct_map = {"inventory": 0.4, "capex": 0.5, "debt": 0.3}
    with patch.object(ch, "_stock_level_pct_map", return_value=pct_map):
        r = ch.get_block_cycle_heat("测试", None)
    assert r is not None
    assert r.hot_count == 0
    assert r.hot_indicators == []


def test_none_when_no_stock_samples():
    with patch.object(ch, "_stock_level_pct_map", return_value=None):
        r = ch.get_block_cycle_heat("测试", None)
    assert r is None


def test_stock_level_aggregation_takes_max_percentile():
    """个股级聚合：指标分位取成分股最大值（任一成分股过热 → 板块边缘过热）"""
    def _fake_series(code):
        if code == "600000":
            return _yoy_series(vals=[0.0, 0.1] + [5.0] * 38)   # 分位 1.0
        return _yoy_series(vals=[0.0, 0.1] + [0.5] * 38)       # 分位 ≈ (1/40)
    with patch.object(ch, "_block_codes", return_value=["600000", "000001"]), \
         patch.object(ch, "_fetch_stock_yoy_series", side_effect=_fake_series):
        m = ch._stock_level_pct_map("测试", None, ch._CONFIG)
    assert m is not None
    assert m["inventory"] > 0.6 and m["capex"] > 0.6 and m["debt"] > 0.6  # max 取高位股


def test_no_lookahead_with_as_of():
    # 前 32 期递减低温(48→17)，后 8 期高温 300；截断到低温区末尾 → 分位低
    vals = [float(v) for v in range(48, 16, -1)] + [300.0] * 8
    df = _yoy_series(n=40, vals=vals)
    as_of = df["date"].iloc[31]  # 截断后恰剩 32 期 ≥ min_obs=32
    with patch.object(ch, "_block_codes", return_value=["600000"]), \
         patch.object(ch, "_fetch_stock_yoy_series", return_value=df):
        m = ch._stock_level_pct_map("测试", as_of, ch._CONFIG)
    assert m is not None
    assert m["inventory"] < 0.10  # 未混入 future 高温段
    assert "inventory" in m and m["inventory"] is not None


def test_few_obs_leads_to_no_percentile():
    df = _yoy_series(n=10, vals=list(range(10)))  # < min_obs=32
    with patch.object(ch, "_block_codes", return_value=["600000"]), \
         patch.object(ch, "_fetch_stock_yoy_series", return_value=df):
        m = ch._stock_level_pct_map("测试", None, ch._CONFIG)
    assert m is None  # 全部个股观测不足 → 板块无有效分位