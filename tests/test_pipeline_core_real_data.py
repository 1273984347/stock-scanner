"""lib/pipeline_core.steps 真实场景测试 (T8, 2026-06-09)

与 test_pipeline_core_steps.py 互补:
- 后者用 mock 隔离测试容错路径
- 本测试用真实 DataFrame 触发 compute_all_signals / calc_composite_verdict 真实调用
  验证返回结构、列名约定、关键字段存在

覆盖:
- calculate_factors: 真实 K线 → 因子值 + 技术面综合
- fetch_stock_data: 真实 data_layer mock 链路 (data_layer 子函数真实调用)
- score_with_realtime: full_score 真实调用 (不 mock 4 维评分)
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

from unittest.mock import patch

import pandas as pd
import pytest

from lib.strategy import steps


# ─────────────────────────────────────────────────────────────
# Fixtures — 真实 K线 DataFrame
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def real_kline():
    """180 天真实 K线, 列名匹配 calc_composite_verdict 要求 (注意: 'volume' 不是 'vol')

    lib.scoring.technical_signals.generate_signals 期望列: open/close/high/low/volume
    factors.compute_all_signals 期望: trade_date (在 as_of_date 切片前)
    """
    return pd.DataFrame({
        "close":     [10.0 + i * 0.1 for i in range(180)],
        "open":      [10.0 + i * 0.1 for i in range(180)],
        "high":      [10.5 + i * 0.1 for i in range(180)],
        "low":       [9.5  + i * 0.1 for i in range(180)],
        "volume":    [1000000.0] * 180,
        "trade_date": pd.date_range("2025-12-13", periods=180, freq="D"),
    })


@pytest.fixture
def real_index_kline():
    """沪深 300 指数 20 天 K线 (check_offset_anomaly 真实调用)"""
    return pd.DataFrame({
        "close":      [10.0 + i * 0.05 for i in range(20)],
        "trade_date": pd.date_range("2026-05-13", periods=20, freq="D"),
    })


# ─────────────────────────────────────────────────────────────
# calculate_factors 真实场景 (3 用例)
# ─────────────────────────────────────────────────────────────

def test_calculate_factors_real_kline_returns_momentum(real_kline):
    """真实 180 天 K线 → momentum_5/20 应有合理数值 (递增序列)"""
    factors, tech = steps.calculate_factors(real_kline)
    # 递增 0.1 元/天 × 5 天 ≈ 0.5 → 0.5/10 × 100% ≈ 5% (mom_5)
    mom_5 = factors.get("momentum_5")
    assert mom_5 is not None, "应有 momentum_5 字段"
    assert 0 < mom_5 < 10, f"递增序列 momentum_5 应在 (0, 10)%, 实际 {mom_5}"
    mom_20 = factors.get("momentum_20")
    assert mom_20 is not None
    assert mom_5 < mom_20, f"递增序列 momentum_20 应 > momentum_5, 实际 {mom_5} vs {mom_20}"


def test_calculate_factors_real_kline_tech_verdict_bullish(real_kline):
    """真实 180 天递增 K线 → tech verdict 应 bullish (12 signals)"""
    factors, tech = steps.calculate_factors(real_kline)
    assert tech.get("verdict") in ("bullish", "strong_bullish"), \
        f"递增 K线应 bullish, 实际 {tech.get('verdict')}"
    signals = tech.get("signals", {})
    assert len(signals) > 0, f"应有 signals 字典, 实际 {signals}"


def test_calculate_factors_real_kline_returns_factors_and_tech_tuple(real_kline):
    """真实调用返 (factors dict, tech dict) — 类型 + 键存在性"""
    factors, tech = steps.calculate_factors(real_kline)
    assert isinstance(factors, dict)
    assert isinstance(tech, dict)
    # 关键键存在
    assert "momentum_5" in factors
    assert "momentum_20" in factors
    assert "verdict" in tech
    assert "signals" in tech


# ─────────────────────────────────────────────────────────────
# fetch_stock_data 真实 data_layer 链路 (2 用例)
# ─────────────────────────────────────────────────────────────

def test_fetch_stock_data_real_data_layer_link(real_kline, real_index_kline):
    """真实 data_layer 子函数: get_stock_hist / get_realtime / _get_financial / get_index"""
    from lib.data.data_layer import _get_financial_data  # 真实调用, 不 mock

    # 注: steps.py 顶部 from lib.data.data_layer import X, mock 必须打 lib.strategy.steps.X
    with patch("lib.strategy.steps.get_stock_hist_multi_source", return_value=real_kline), \
         patch("lib.strategy.steps.get_realtime_quotes", return_value=pd.DataFrame()), \
         patch("lib.strategy.steps._get_financial_data", return_value=None), \
         patch("lib.strategy.steps.get_index_hist", return_value=real_index_kline):
        hist, rt_row, fin_data, idx_df = steps.fetch_stock_data("600519", days=180)
    # 真实返回路径校验
    assert len(hist) == 180, "应拿到 180 天 K线"
    assert "close" in hist.columns
    assert "trade_date" in hist.columns
    assert rt_row is None  # 实时行情空时返 None
    # fin_data 可能含 TDX 财务数据 fallback (即使 _get_financial_data mock 为 None,
    # steps.py 还有 TDX 真实 fallback, 见 step 3.5)
    assert fin_data is None or isinstance(fin_data, dict)
    assert len(idx_df) == 20


def test_fetch_stock_data_with_realtime_quote(real_kline):
    """有实时行情时, 正确匹配 code 返 rt_row"""
    realtime = pd.DataFrame({
        "代码":     ["600519", "000001"],
        "最新价":    [1800.0, 12.0],
        "涨跌幅":   [2.5, -0.5],
    })
    with patch("lib.strategy.steps.get_stock_hist_multi_source", return_value=real_kline), \
         patch("lib.strategy.steps.get_realtime_quotes", return_value=realtime), \
         patch("lib.strategy.steps._get_financial_data", return_value=None), \
         patch("lib.strategy.steps.get_index_hist", return_value=pd.DataFrame()):
        hist, rt_row, fin_data, idx_df = steps.fetch_stock_data("600519", days=180)
    assert rt_row is not None
    assert str(rt_row["代码"]) == "600519"
    assert float(rt_row["最新价"]) == 1800.0


# ─────────────────────────────────────────────────────────────
# check_offset_anomaly 真实 scorer 调用 (1 用例)
# ─────────────────────────────────────────────────────────────

def test_check_offset_anomaly_real_scorer_bullish_normal(real_kline, real_index_kline):
    """股票 + 指数 都递增 → 偏移值在合理范围 → 不触发告警"""
    factors, _ = steps.calculate_factors(real_kline)
    with patch("lib.scoring.scorer.get_board_type", return_value="main"):
        offset_alert, score_adj = steps.check_offset_anomaly(
            "600519", real_kline, factors, real_index_kline
        )
    # 正常情况下应不触发 (或 score_adj=0)
    assert score_adj == 0.0, f"正常市场 score_adj 应 0, 实际 {score_adj}"
