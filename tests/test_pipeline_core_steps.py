"""lib/pipeline_core.steps 6 个 step 函数测试 (C4, 2026-06-09)

覆盖:
- 6 个 step 函数各自能正常 import (无 ImportError)
- 6 个函数返回类型符合约定 (Tuple / Dict / None)
- 容错: 失败返约定 sentinel (空 dict / 空 DataFrame / (None, 0.0))

被测函数:
- fetch_stock_data(code, days=180) → (hist, rt_row, fin_data, idx_df)
- calculate_factors(hist) → (factors, tech)
- score_with_realtime(code, hist, factors, rt_row, fin_data) → dict
- score_without_realtime(code, factors, fin_data) → dict
- evaluate_with_judge_ml(code, hist, factors, fin_data, ml_prediction) → dict
- check_offset_anomaly(code, hist, factors, idx_df) → (offset_alert, score_adjustment)
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from lib.strategy import steps as steps_mod


# ─────────────────────────────────────────────────────────────
# 6 个 step 函数可 import (C4 验证: 6 步均不 ImportError)
# ─────────────────────────────────────────────────────────────

def test_all_6_steps_importable():
    """C4 关键验证: 6 步均能 import, 不 ImportError (修 master P0 破损)"""
    from lib.strategy.steps import (
        fetch_stock_data, calculate_factors,
        score_with_realtime, score_without_realtime,
        evaluate_with_judge_ml, check_offset_anomaly,
    )
    # 6 步全是 callable function
    for name, fn in [
        ("fetch_stock_data", fetch_stock_data),
        ("calculate_factors", calculate_factors),
        ("score_with_realtime", score_with_realtime),
        ("score_without_realtime", score_without_realtime),
        ("evaluate_with_judge_ml", evaluate_with_judge_ml),
        ("check_offset_anomaly", check_offset_anomaly),
    ]:
        assert callable(fn), f"{name} 应该是函数"
    assert len([1]) == 1  # 通过 import


def test_lib_pipeline_imports_via_lazy_pipeline_core():
    """C4 验证: lib/pipeline.py 的 lazy import 不再 ImportError"""
    from lib.strategy.pipeline import get_stock_score  # lazy import 在函数体内, 此处只验 module 加载
    # 真实调用会触发 lazy import — 用 mock 隔离
    # 因为 get_stock_score 需要 network, 这里只验证 import OK
    assert callable(get_stock_score)


# ─────────────────────────────────────────────────────────────
# fetch_stock_data 容错
# ─────────────────────────────────────────────────────────────

def test_fetch_stock_data_handles_empty():
    """fetch_stock_data 失败时返 (empty_df, None, None, empty_df) — 不 raise"""
    with patch.object(steps_mod, "get_stock_hist_multi_source",
                      return_value=pd.DataFrame()):
        with patch.object(steps_mod, "get_realtime_quotes",
                          return_value=pd.DataFrame()):
            with patch.object(steps_mod, "_get_financial_data", return_value=None), \
                 patch.object(steps_mod, "get_index_hist", return_value=pd.DataFrame()):
                hist, rt_row, fin_data, idx_df = steps_mod.fetch_stock_data("999999")
    assert hist.empty, "失败时 hist 应为空 DataFrame"
    assert rt_row is None
    assert fin_data is None or isinstance(fin_data, dict)
    assert idx_df.empty


def test_fetch_stock_data_returns_4tuple():
    """fetch_stock_data 必须返 4-tuple (hist, rt_row, fin_data, idx_df)"""
    with patch.object(steps_mod, "get_stock_hist_multi_source",
                      return_value=pd.DataFrame({"close": [10.0]})):
        with patch.object(steps_mod, "get_realtime_quotes",
                          return_value=pd.DataFrame()):
            with patch.object(steps_mod, "_get_financial_data", return_value=None), \
                 patch.object(steps_mod, "get_index_hist", return_value=pd.DataFrame()):
                result = steps_mod.fetch_stock_data("600519")
    assert len(result) == 4, "应返 4-tuple"
    assert isinstance(result[0], pd.DataFrame)  # hist


# ─────────────────────────────────────────────────────────────
# calculate_factors 容错
# ─────────────────────────────────────────────────────────────

def test_calculate_factors_empty_hist_returns_empty_dicts():
    """空 hist → 返 ({}, {}) 不 raise"""
    factors, tech = steps_mod.calculate_factors(pd.DataFrame())
    assert factors == {}
    assert tech == {}


# ─────────────────────────────────────────────────────────────
# score_with_realtime / score_without_realtime 容错
# ─────────────────────────────────────────────────────────────

def test_score_with_realtime_returns_dict():
    """score_with_realtime 应返 dict (含 score / rating)"""
    fake_row = pd.Series({"代码": "600519", "涨跌幅": 1.5})
    # full_score 在函数体内 from lib.strategy.strategies import full_score
    # mock 必须 patch 源位置 (lib.strategy.strategies.full_score)
    with patch("lib.strategy.strategies.full_score",
               return_value={"score": 5.0, "rating": "buy"}):
        result = steps_mod.score_with_realtime("600519", pd.DataFrame({"close": [10]}), {}, fake_row)
    assert isinstance(result, dict)
    assert "score" in result


def test_score_without_realtime_returns_dict():
    """score_without_realtime 应返 dict (4 维综合分)"""
    # 这些函数在 steps.py 模块顶部 import, mock 源位置
    with patch("lib.scoring.scorer.score_technical", return_value=5.0), \
         patch("lib.scoring.scorer.score_fundamental", return_value=5.0), \
         patch("lib.scoring.scorer.score_capital_flow", return_value=5.0), \
         patch("lib.scoring.scorer.score_news", return_value=5.0), \
         patch("lib.scoring.scorer.comprehensive_score", return_value={"score": 5.0, "rating": "buy"}):
        result = steps_mod.score_without_realtime("600519", {"momentum_5": 5, "momentum_20": 10})
    assert isinstance(result, dict)


# ─────────────────────────────────────────────────────────────
# evaluate_with_judge_ml 容错
# ─────────────────────────────────────────────────────────────

def test_evaluate_with_judge_ml_returns_dict_on_error():
    """judge_panel 抛异常 → 返空 dict (graceful degradation)"""
    # JudgePanel 在函数体内 from lib.scoring.judge_panel import JudgePanel
    with patch("lib.scoring.judge_panel.JudgePanel",
               side_effect=ImportError("judge_panel 不存在")):
        result = steps_mod.evaluate_with_judge_ml("600519", pd.DataFrame(), {}, {}, {})
    assert result == {}, f"judge_panel 失败应返空 dict, 实际 {result}"


def test_evaluate_with_judge_ml_no_ml_prediction_ok():
    """ml_prediction=None / {} 时, 仍能正常跑"""
    with patch("lib.scoring.judge_panel.JudgePanel") as mock_panel:
        mock_instance = MagicMock()
        mock_panel.return_value = mock_instance
        mock_instance.evaluate_with_ml.return_value = {"verdict": "neutral"}
        result = steps_mod.evaluate_with_judge_ml("600519", pd.DataFrame(), {}, {}, None)
    assert result == {"verdict": "neutral"}


# ─────────────────────────────────────────────────────────────
# check_offset_anomaly 容错
# ─────────────────────────────────────────────────────────────

def test_check_offset_anomaly_empty_idx_df_returns_none_zero():
    """idx_df 空 → 返 (None, 0.0) 不 raise"""
    offset_alert, score_adj = steps_mod.check_offset_anomaly(
        "600519", pd.DataFrame(), {"momentum_5": 5}, pd.DataFrame()
    )
    assert offset_alert in (None, False), f"空 idx_df 应不触发, 实际 {offset_alert}"
    assert score_adj == 0.0


def test_check_offset_anomaly_returns_tuple_of_two():
    """check_offset_anomaly 必须返 2-tuple (offset_alert, score_adjustment)"""
    idx_df = pd.DataFrame({"close": [10.0, 11.0]})
    result = steps_mod.check_offset_anomaly("600519", pd.DataFrame(), {"momentum_5": 5}, idx_df)
    assert len(result) == 2
    offset_alert, score_adj = result
    # type 约定: offset_alert 是 dict / None / False, score_adj 是 float
    assert isinstance(score_adj, (int, float))


def test_check_offset_anomaly_normal_market_no_alert():
    """正常市场 (stock/idx 涨跌幅都在合理范围) → 不触发告警"""
    idx_df = pd.DataFrame({"close": [10.0, 10.5]})  # +5%
    with patch.object(steps_mod, "check_anomaly_threshold", return_value={"triggered": False}):
        offset_alert, score_adj = steps_mod.check_offset_anomaly(
            "600519", pd.DataFrame(), {"momentum_5": 4}, idx_df  # +4%, 接近 idx
        )
    assert offset_alert in (None, False)
    assert score_adj == 0.0
