"""坑表 P-103 机器判据（2026-11-19）：评分按代码写回，禁止 NaN。

现象：盘后扫描的龙虎榜候选里 `000150 *ST宜康`、`000416 *ST民控`
`综合评分 = NaN`。根因不是评分抛异常，而是**两份成员不同的列表按位置对齐**：

* ``rt_rows`` 只包含实时行情里有的代码（*ST/停牌股不在快照里）；
* ``scores`` 却遍历全部候选代码，缺失项 ``dict.get(code, {})`` 取到 None。

NaN 进入 DataFrame 后参与 Top-N 排序会破坏顺序且不报错。修法是按代码写回，
并给「无实时行情 / 评分失败」确定值（0.0）+ 显式 ``评分状态``。
"""
import pandas as pd
import pytest

import lib.strategy.strategies as st


def _result():
    return pd.DataFrame({
        "代码": ["600519", "000150", "000416"],
        "名称": ["贵州茅台", "*ST宜康", "*ST民控"],
    })


def test_missing_realtime_gets_zero_not_nan(monkeypatch):
    monkeypatch.setattr(st, "batch_quick_score",
                        lambda rows, **kw: {"600519": {"score": 3.0, "rating": "积极"}})
    out = st._attach_quick_scores(
        _result(), ["600519", "000150", "000416"],
        {"600519": {"最新价": 1442.66}}, {}, {})
    assert out["综合评分"].tolist() == [3.0, 0.0, 0.0]
    assert out["评分状态"].tolist() == ["积极", "无实时行情", "无实时行情"]
    assert not out["综合评分"].isna().any()


def test_failed_score_gets_zero_not_nan(monkeypatch):
    """评分异常写入 {"score": None, "rating": "error"} 也不能漏成 NaN。"""
    monkeypatch.setattr(st, "batch_quick_score", lambda rows, **kw: {
        "600519": {"score": None, "rating": "error"},
        "000150": {"score": float("nan"), "rating": "error"},
    })
    out = st._attach_quick_scores(
        _result(), ["600519", "000150", "000416"],
        {c: {"最新价": 10.0} for c in ("600519", "000150")}, {}, {})
    assert out["综合评分"].tolist() == [0.0, 0.0, 0.0]
    assert out["评分状态"].tolist() == ["评分失败", "评分失败", "无实时行情"]
    assert not out["综合评分"].isna().any()


def test_lhb_pipeline_has_no_nan_score(monkeypatch):
    """真实策略链：龙虎榜里有不在实时快照中的代码时，输出不得含 NaN。"""
    from config import get_config

    lhb = pd.DataFrame({
        "代码": ["600519", "000150"],
        "名称": ["贵州茅台", "*ST宜康"],
        "买入金额": [1e8, 5e7],
        "上榜原因": ["日涨幅偏离值达到7%", "连续三日跌幅偏离"],
    })
    realtime = pd.DataFrame({
        "代码": ["600519"], "名称": ["贵州茅台"],
        "最新价": [1442.66], "涨跌幅": [1.0], "成交量": [1e6], "成交额": [1e9],
    })
    monkeypatch.setattr(st, "_retry_api_call", lambda *a, **k: lhb)
    monkeypatch.setattr(st, "get_realtime_quotes", lambda: realtime)
    monkeypatch.setattr(st, "_batch_fund_flow", lambda codes: {})
    monkeypatch.setattr(st, "_batch_get_news_signals", lambda codes: {})
    monkeypatch.setattr(st, "batch_quick_score",
                        lambda rows, **kw: {"600519": {"score": 2.0, "rating": "谨慎"}})

    out = st.get_lhb_candidates(get_config())
    assert len(out) == 2, "无实时行情的候选应保留信号，不静默丢弃"
    assert not out["综合评分"].isna().any()
    assert out.loc[out["代码"] == "000150", "评分状态"].iloc[0] == "无实时行情"


def test_no_positional_alignment_remains():
    """静态守卫：两处策略不得再出现「遍历全 codes 取 score」的旧模式。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "lib/strategy/strategies.py").read_text(encoding="utf-8")
    assert 'score_results.get(code, {}).get("score")' not in src, \
        "旧的按位置对齐评分写回仍然存在"
    assert "_attach_quick_scores(result, codes, row_map" in src
    assert src.count("_attach_quick_scores(result, codes, row_map") == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
