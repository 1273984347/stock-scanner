# tests/test_cycle_heat.py
from unittest.mock import patch

import pandas as pd

import lib.cycle_heat as ch


def _series(n=40, vals=None):
    # freq="QE" 需 pandas>=2.2（当前环境 2.3.3 满足）；<2.2 的旧环境请改用 "Q"
    dates = pd.date_range("2015-03-31", periods=n, freq="QE").strftime("%Y%m%d")
    v = vals if vals is not None else list(range(n))
    return pd.DataFrame({
        "date": list(dates),
        "inventory_yoy": v,
        "revenue_yoy": v,
        "capex_yoy": v,
        "debt_yoy": v,
        "n_stocks": [20] * n,
    })


def test_hot_count_three_when_all_top_decile():
    # 最新 36 期均为 9.0，分位 = (w <= 9.0).mean() = 1.0 ≥ 0.85 → 三指标全过热
    df = _series(vals=[0.0, 0.1, 0.0, 0.1] + [9.0] * 36)
    with patch.object(ch, "fetch_block_financial_series", return_value=df):
        r = ch.get_block_cycle_heat("测试", None)
    assert r is not None
    assert r.hot_count == 3
    assert set(r.hot_indicators) == {"inventory_yoy", "capex_yoy", "debt_yoy"}
    assert r.inventory_pct > 0.85
    assert r.n_obs >= 32


def test_neutral_when_few_obs():
    df = _series(n=10, vals=list(range(10)))
    with patch.object(ch, "fetch_block_financial_series", return_value=df):
        r = ch.get_block_cycle_heat("测试", None)
    assert r is not None
    assert r.hot_count == 0
    assert r.n_obs == 10
    assert r.inventory_pct == 0.5
    assert r.capex_pct == 0.5
    assert r.debt_pct == 0.5


def test_no_lookahead_with_as_of():
    # 前 32 期递减低温(48→17)，后 8 期高温 300；截断到低温区末尾
    vals = [float(v) for v in range(48, 16, -1)] + [300.0] * 8
    df = _series(n=40, vals=vals)
    as_of = df["date"].iloc[31]                     # 截断后恰剩 32 期 ≥ min_obs=32
    with patch.object(ch, "fetch_block_financial_series", return_value=df):
        r = ch.get_block_cycle_heat("测试", as_of)
    assert r is not None
    assert r.n_obs == 32                            # 未混入未来数据
    assert r.inventory_pct < 0.05                   # (w<=17).mean() = 1/32 ≈ 0.031
