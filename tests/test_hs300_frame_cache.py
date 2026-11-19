"""坑表 P-107 机器判据（2026-11-19）：同一慢接口在打分卡内被两处重复拉取。

profile 显示单次 ScoreCard.score 13.8s 中 6.2s 花在
`akshare.stock_zh_index_daily`（L1 趋势与市场收益各一次，各 ~3.1s）。
沪深300 日线在同一轮任务里内容不变，加 10 分钟 TTL 共享缓存；
`as_of_date` 截断仍由调用方负责——缓存的是原始帧，不破坏防前视约束。
"""
import pandas as pd
import pytest

import lib.scoring.scorecard as sc


@pytest.fixture(autouse=True)
def _reset_hs300_cache(monkeypatch):
    monkeypatch.setattr(sc, "_hs300_frame", None)
    monkeypatch.setattr(sc, "_hs300_frame_time", 0.0)
    yield


def _frame():
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=120, freq="B"),
        "close": [100 + i * 0.5 for i in range(120)],
        "volume": [1e6] * 120,
    })


def test_hs300_frame_fetched_once(monkeypatch):
    import akshare as ak
    calls = []

    def fake(symbol):
        calls.append(symbol)
        return _frame()

    monkeypatch.setattr(ak, "stock_zh_index_daily", fake)

    for _ in range(3):
        sc._get_market_return(20)
    for _ in range(3):
        sc.MarketFilter._compute_l1(60)

    assert calls == ["sh000300"], f"两处调用方应共享一份缓存，实际 {calls}"


def test_as_of_date_still_truncates_after_cache(monkeypatch):
    """缓存原始帧，不改变逐调用方的 as_of_date 截断语义。"""
    import akshare as ak
    monkeypatch.setattr(ak, "stock_zh_index_daily", lambda symbol: _frame())

    full = sc._get_market_return(20)
    partial = sc._get_market_return(20, as_of_date="2025-02-15")
    assert full is not None
    assert partial is not None
    assert abs(full - partial) > 1e-6, "as_of_date 截断失效——缓存引入了前视风险"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
