"""P-101 机器判据（2026-11-19）：`ml_ranking["data"]` 的单一形状 = list[dict]。

事故：数据恢复后第一次真实盘后扫描在 `pipeline.py` 崩溃——
`if ml_ranking.get("data")` 对 DataFrame 做布尔判断抛
`ValueError: The truth value of a DataFrame is ambiguous`。
根因不是这一行：`_enrich_with_ml_scores` 把 DataFrame 塞进 `"data"`，
而它的**三个**消费方都按 list[dict] 使用：

1. 本模块 `_apply_regime_adjustment(candidates: list)`：`c.copy()` / `c.get()`
2. 本模块 `if ml_ranking.get("data")`：DataFrame 布尔判断必抛
3. `reporter/report_generator.py`：`for item in data[:10]`，DataFrame 迭代得到列名

同文件其实已经产出同形的 `top10`。修法是统一形状，而不是在调用处加
`if isinstance(df, pd.DataFrame)` 特判——那会让契约继续分裂。
"""
import json

import pandas as pd
import pytest

import lib.strategy.pipeline as pipeline


def _hist(n=100):
    return pd.DataFrame({
        "date": pd.date_range("2026-06-01", periods=n, freq="B"),
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1e6,
    })


class _Predictor:
    def predict_single(self, factors):
        return {"score": 0.9, "consensus": "BUY", "confidence": 0.8}


@pytest.fixture
def ml_ranking(monkeypatch):
    """绕开真实模型/取数，只验证返回形状契约。"""
    monkeypatch.setattr(pipeline, "get_stock_hist_multi_source",
                        lambda *a, **k: _hist())
    monkeypatch.setattr(pipeline, "compute_all_signals",
                        lambda h, code=None: {"factors": {
                            "momentum_20": 1.0, "momentum_5": 0.5,
                            "rsi_14": 55.0, "volatility_20": 20.0,
                        }})
    monkeypatch.setattr("lib.ml.ml_predictor.get_predictor", lambda: _Predictor())
    results = {"测试策略": pd.DataFrame({"代码": ["999999"], "名称": ["形状测试"]})}
    return pipeline._enrich_with_ml_scores(results)


def test_data_is_list_of_dicts(ml_ranking):
    assert isinstance(ml_ranking["data"], list), \
        f"data 必须是 list[dict]，实际 {type(ml_ranking['data'])}"
    assert all(isinstance(item, dict) for item in ml_ranking["data"])
    assert ml_ranking["data"][0]["代码"] == "999999"


def test_data_and_top10_share_one_shape(ml_ranking):
    """同一份内容不许有两种表示——这是旧实现的直接病根。"""
    assert ml_ranking["data"] == ml_ranking["top10"]


def test_data_is_json_serializable(ml_ranking):
    """DataFrame 不可直接 JSON 化；scan_result 要能走 API / 审计日志。"""
    assert json.loads(json.dumps(ml_ranking["data"]))[0]["代码"] == "999999"


def test_regime_adjustment_consumes_data(ml_ranking):
    """消费方一：市场状态调整按 list[dict] 逐条处理。"""
    adjusted = pipeline._apply_regime_adjustment(ml_ranking["data"],
                                                 pipeline.MEAN_REVERSION)
    assert isinstance(adjusted, list) and isinstance(adjusted[0], dict)
    assert "ML置信度" in adjusted[0]


def test_empty_data_uses_none_not_empty_dataframe(monkeypatch):
    monkeypatch.setattr(pipeline, "get_stock_hist_multi_source",
                        lambda *a, **k: pd.DataFrame())
    out = pipeline._enrich_with_ml_scores(
        {"测试": pd.DataFrame({"代码": ["888888"], "名称": ["无数据"]})})
    assert out["data"] is None and out["n_passed"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
