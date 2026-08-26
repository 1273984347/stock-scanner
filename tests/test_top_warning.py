# tests/test_top_warning.py
from unittest.mock import patch
import pandas as pd
import numpy as np

import lib.top_warning as tw


def _prices(closes):
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "close": closes,
        "high": np.array(closes) * 1.01,
        "low": np.array(closes) * 0.99,
        "volume": [1e6] * n,
    })


def test_breakdown_true_when_below_ma20_and_ma60():
    closes = [10.0] * 50 + [9.0] * 10   # MA20=(10×10+9×10)/20=9.5；MA60=(10×50+9×10)/60≈9.83；9.0 双双跌破
    assert tw.check_price_breakdown(_prices(closes), mode="ma20_60") is True


def test_breakdown_false_when_above_mas():
    closes = [10.0] * 50 + [11.0] * 10  # 最新 10 根高于两均线
    assert tw.check_price_breakdown(_prices(closes), mode="ma20_60") is False


def test_breakdown_unknown_mode_raises():
    import pytest
    with pytest.raises(ValueError):
        tw.check_price_breakdown(_prices([10.0] * 60), mode="ma_none")


def test_detect_top_warning_exit_all_end_to_end():
    from lib.cycle_heat import HotResult
    closes = [10.0] * 50 + [9.0] * 10
    hot3 = HotResult(block="白酒", hot_count=3,
                     hot_indicators=["inventory_yoy", "capex_yoy", "debt_yoy"],
                     inventory_pct=0.9, capex_pct=0.9, debt_pct=0.9,
                     n_obs=40, as_of="20240101")
    with patch("lib.cycle_heat.get_block_cycle_heat", return_value=hot3), \
         patch("lib.data_layer.get_stock_blocks", return_value=["白酒"]):
        r = tw.detect_top_warning("600519", price_df=_prices(closes))
    assert r is not None and r.triggered is True
    assert r.action == "exit_all" and r.block == "白酒"


def test_detect_top_warning_reduce_half_when_two_hot():
    from lib.cycle_heat import HotResult
    closes = [10.0] * 50 + [9.0] * 10
    hot2 = HotResult(block="白酒", hot_count=2,
                     hot_indicators=["inventory_yoy", "capex_yoy"],
                     inventory_pct=0.9, capex_pct=0.9, debt_pct=0.2,
                     n_obs=40, as_of="20240101")
    with patch("lib.cycle_heat.get_block_cycle_heat", return_value=hot2), \
         patch("lib.data_layer.get_stock_blocks", return_value=["白酒"]):
        r = tw.detect_top_warning("600519", price_df=_prices(closes))
    assert r is not None and r.action == "reduce_half"


def test_no_warning_without_breakdown():
    from lib.cycle_heat import HotResult
    closes = [10.0] * 50 + [11.0] * 10   # 未破位
    hot3 = HotResult(block="白酒", hot_count=3,
                     hot_indicators=["inventory_yoy", "capex_yoy", "debt_yoy"],
                     inventory_pct=0.9, capex_pct=0.9, debt_pct=0.9,
                     n_obs=40, as_of="20240101")
    with patch("lib.cycle_heat.get_block_cycle_heat", return_value=hot3), \
         patch("lib.data_layer.get_stock_blocks", return_value=["白酒"]):
        r = tw.detect_top_warning("600519", price_df=_prices(closes))
    assert r is not None and r.action == "hold" and r.triggered is False


def test_service_get_warning_with_price_df_does_not_read_local_files():
    svc = tw.TopWarningService()
    with patch("lib.top_warning._price_df") as m_price, \
         patch("lib.data_layer.get_stock_blocks", return_value=["白酒"]), \
         patch("lib.cycle_heat.get_block_cycle_heat", return_value=None):
        svc.get_warning("600519", as_of="20240101", price_df=_prices([10.0] * 60))
    assert m_price.call_count == 0   # price_df 路径不得触发本地 K 线读取


def test_service_cooldown_logic():
    svc = tw.TopWarningService()
    svc.mark_exit("白酒", "2024-06-30")
    assert svc.is_block_cooling("白酒", "2024-05-01") is True
    assert svc.is_block_cooling("白酒", "2024-07-01") is False
    assert svc.is_block_cooling("银行", "2024-05-01") is False


def test_dynamic_exit_manager_applies_exit_all():
    from lib.dynamic_exit import DynamicExitManager
    from lib.top_warning import TopWarningResult
    mgr = DynamicExitManager()
    mgr.update_market_state("TREND_UP", 0.2)
    before = mgr.get_position_pct()
    assert before > 0
    mgr.apply_top_warning(TopWarningResult(
        block="白酒", code="600519", triggered=True, hot_count=3,
        hot_indicators=["inventory_yoy", "capex_yoy", "debt_yoy"],
        breakdown=True, action="exit_all", reasoning="", as_of="20240101"))
    assert mgr.get_position_pct() == 0.0
    assert mgr.get_exit_days() == 0
    assert mgr.get_top_warning_state()["action"] == "exit_all"


def test_dynamic_exit_manager_reduce_half():
    from lib.dynamic_exit import DynamicExitManager
    from lib.top_warning import TopWarningResult
    mgr = DynamicExitManager()
    mgr.update_market_state("TREND_UP", 0.2)
    base = mgr.get_position_pct()
    assert base > 0
    mgr.apply_top_warning(TopWarningResult(
        block="白酒", code="600519", triggered=True, hot_count=2,
        hot_indicators=["inventory_yoy", "capex_yoy"], breakdown=True,
        action="reduce_half", reasoning="", as_of="20240101"))
    assert abs(mgr.get_position_pct() - base * 0.5) < 1e-9
