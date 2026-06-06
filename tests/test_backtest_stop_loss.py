"""P2-6 backtest stop loss scenarios with pre-existing positions"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import datetime


def _make_kline(start_date, n=35, start_price=100.0, slope=0.0):
    """Construct n-day K-line DataFrame with datetime trade_date (required by broker).

    Columns (required by broker/data-layer):
      - trade_date : datetime (datetime dtype, not string — broker compares with Timestamp)
      - open, high, low, close, volume : float
      - pre_close  : float (required by broker.submit_order for limit up/down price calc;
                             first row is NaN, subsequent rows = previous close)

    All columns must be present. Missing any column used in df["col"] direct indexing
    will cause KeyError or silent incorrect behavior.
    """
    dates = pd.date_range(start=start_date, periods=n, freq="B")
    prices = [start_price + slope * i for i in range(n)]
    # pre_close: shift close by 1, first row has no pre_close -> use close
    pre_close = [float("nan")] + prices[:-1]
    return pd.DataFrame({
        "trade_date": dates,
        "open": prices,
        "high": [p * 1.02 for p in prices],
        "low": [p * 0.98 for p in prices],
        "close": prices,
        "volume": [1e6] * n,
        "pre_close": pre_close,
    })


def test_initial_position_triggers_hard_stop_loss_on_day1():
    """Pre-existing position triggers hard stop loss on backtest day 1.

    Hard stop threshold = stop_loss_atr_mult * 0.05 = 2.0 * 0.05 = 0.10 (10%).
    Buy at 100, price on day1 = 89 (-11%) -> triggers hard stop.
    """
    from backtest.core import run_factor_backtest

    base_date = datetime(2024, 1, 1)
    df_stock = _make_kline(base_date, n=35, start_price=100.0, slope=0.0)
    df_stock.loc[0, "close"] = 89.0  # -11% loss triggers 10% hard stop

    with patch("backtest.core.get_stock_data") as mock_get:
        mock_get.return_value = df_stock

        result = run_factor_backtest(
            symbols=["000001"],
            days=35,
            initial_cash=1_000_000,
            stop_loss_atr_mult=2.0,
            initial_positions=[
                {"code": "000001", "shares": 1000, "buy_price": 100.0}
            ],
        )

    assert result is not None
    stop_trades = [t for t in result.get("trades", []) if "stop" in t.get("reason", "").lower() or "止" in t.get("reason", "")]
    assert len(stop_trades) > 0, f"Stop loss not triggered: {result.get('trades', [])}, exit_reasons={result.get('exit_reasons', {})}"


def test_position_hard_stop_loss_during_holding():
    """Pre-existing position held for several days then triggers hard stop loss.

    Hard stop threshold = 10%. Buy at 99, day4 price=89 -> pnl=-10.1% triggers.
    """
    from backtest.core import run_factor_backtest

    base_date = datetime(2024, 1, 1)
    df_stock = _make_kline(base_date, n=35, start_price=100.0, slope=0.0)
    for i in range(3):
        df_stock.loc[i, "close"] = 99.0  # days 1-3: stable at 99
    df_stock.loc[3, "close"] = 89.0  # day 4: -10.1% triggers 10% hard stop

    with patch("backtest.core.get_stock_data") as mock_get:
        mock_get.return_value = df_stock

        result = run_factor_backtest(
            symbols=["000001"],
            days=35,
            initial_cash=1_000_000,
            stop_loss_atr_mult=2.0,
            initial_positions=[
                {"code": "000001", "shares": 1000, "buy_price": 99.0}
            ],
        )

    assert result is not None
    stop_trades = [t for t in result.get("trades", []) if "stop" in t.get("reason", "").lower() or "止" in t.get("reason", "")]
    assert len(stop_trades) > 0, f"Stop loss not triggered: trades={result.get('trades', [])}, exit_reasons={result.get('exit_reasons', {})}"
    assert stop_trades[0]["direction"] == "SELL"


def test_no_stop_loss_when_price_rises():
    """No stop loss when price rises or stays stable."""
    from backtest.core import run_factor_backtest

    base_date = datetime(2024, 1, 1)
    df_stock = _make_kline(base_date, n=35, start_price=100.0, slope=0.2)

    with patch("backtest.core.get_stock_data") as mock_get:
        mock_get.return_value = df_stock

        result = run_factor_backtest(
            symbols=["000001"],
            days=35,
            initial_cash=1_000_000,
            stop_loss_atr_mult=2.0,
        )

    assert result is not None
    stop_trades = [t for t in result.get("trades", []) if "stop" in t.get("reason", "").lower() or "止" in t.get("reason", "")]
    assert len(stop_trades) == 0, f"No stop loss expected: {result.get('trades', [])}"


def test_empty_initial_positions_does_not_break():
    """Empty initial_positions list should not break backtest."""
    from backtest.core import run_factor_backtest

    base_date = datetime(2024, 1, 1)
    df_stock = _make_kline(base_date, n=35, start_price=100.0, slope=0.0)

    with patch("backtest.core.get_stock_data") as mock_get:
        mock_get.return_value = df_stock

        result = run_factor_backtest(
            symbols=["000001"],
            days=35,
            initial_cash=1_000_000,
            initial_positions=[],
        )

    assert result is not None
    assert "trade_count" in result or "metrics" in result


# ============== P0-2: 止损价考虑跳空低开 ==============

def _make_kline_with_atr(n=35, entry_price=100.0, entry_atr_pct=2.0, day31_open=97.0, day31_low=99.0):
    """构造 K 线: 30 天横盘 + 第 31 天 entry + 后续天数

    Args:
        n: 总天数
        entry_price: 入场价 (i=30)
        entry_atr_pct: ATR 百分比 (line 150 check_exit_v1_atr 使用)
        day31_open: 第 31 天 (j=31) 开盘价，用于测试跳空
        day31_low: 第 31 天 (j=31) 最低价，需 < stop_price 才能触发 atr_stop
    """
    dates = pd.date_range(start="2024-01-01", periods=n, freq="B")
    closes = [100.0] * 30 + [entry_price] + [100.0] * (n - 31)
    opens = [100.0] * 30 + [entry_price] + [day31_open] + [100.0] * (n - 32)
    highs = [p * 1.01 for p in closes]
    lows = [p * 0.99 for p in closes]
    df = pd.DataFrame({
        "date": dates,
        "open": opens,
        "close": closes,
        "high": highs,
        "low": lows,
        "volume": [1e6] * n,
    })
    df.loc[31, "low"] = day31_low
    return df


def test_v1_atr_exit_uses_gap_down_open():
    """P0-2: 跳空低开时，atr_stop 退出价 = min(open, stop_price)"""
    from backtest.engine import check_exit_v1_atr
    from backtest.utils import add_indicators

    # entry_atr_pct=2 → stop_price = 100 * (1 - 2*2/100) = 96
    # day31 open = 94 (跳空低开) < 96, low = 93 < 96 → 触发 atr_stop
    # 旧逻辑: exit_price = 96
    # 新逻辑: exit_price = min(94, 96) = 94
    df = _make_kline_with_atr(n=35, entry_price=100.0, entry_atr_pct=2.0, day31_open=94.0, day31_low=93.0)
    df = add_indicators(df)

    reason, exit_price = check_exit_v1_atr(df, entry_i=30)
    assert reason == "atr_stop"
    assert abs(exit_price - 94.0) < 1e-6, f"期望 94.0 (gap down open), 实际 {exit_price}"


def test_v1_atr_exit_keeps_stop_price_when_no_gap():
    """P0-2: 无跳空时，atr_stop 退出价 = stop_price"""
    from backtest.engine import check_exit_v1_atr
    from backtest.utils import add_indicators

    # day31 open = 97 (无跳空), low = 95.5 (< 96 stop_price) → 触发 atr_stop
    # 新逻辑: exit_price = min(97, 96) = 96
    df = _make_kline_with_atr(n=35, entry_price=100.0, entry_atr_pct=2.0, day31_open=97.0, day31_low=95.5)
    df = add_indicators(df)

    reason, exit_price = check_exit_v1_atr(df, entry_i=30)
    assert reason == "atr_stop"
    assert abs(exit_price - 96.0) < 1e-6, f"期望 96.0 (stop_price), 实际 {exit_price}"


def test_v2_trend_exit_uses_gap_down_open():
    """P0-2: 跳空低开时，trailing_stop 退出价 = min(open, trailing_stop)"""
    from backtest.engine import check_exit_v2_trend
    from backtest.utils import add_indicators

    # low_10 在 day31 = 99 (前 10 天最低 99)
    # trailing_stop initial = 99
    # day31 open = 95 (跳空低开) < 99, close = 95 < 99 → 触发 trailing_stop
    # 旧逻辑: exit_price = 99 (trailing_stop)
    # 新逻辑: exit_price = min(95, 99) = 95
    df = _make_kline_with_atr(n=35, entry_price=100.0, entry_atr_pct=1.0, day31_open=95.0, day31_low=93.0)
    # 让 low_10 在 day30 之前都是 99
    for i in range(0, 31):
        df.loc[i, "low"] = 99.0
    df.loc[31, "close"] = 95.0  # close < trailing_stop
    df = add_indicators(df)

    reason, exit_price = check_exit_v2_trend(df, entry_i=30)
    assert reason == "trailing_stop"
    assert abs(exit_price - 95.0) < 1e-6, f"期望 95.0 (gap down open), 实际 {exit_price}"

