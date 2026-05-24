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
    from backtest import run_factor_backtest

    base_date = datetime(2024, 1, 1)
    df_stock = _make_kline(base_date, n=35, start_price=100.0, slope=0.0)
    df_stock.loc[0, "close"] = 89.0  # -11% loss triggers 10% hard stop

    with patch("backtest.get_stock_data") as mock_get:
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
    from backtest import run_factor_backtest

    base_date = datetime(2024, 1, 1)
    df_stock = _make_kline(base_date, n=35, start_price=100.0, slope=0.0)
    for i in range(3):
        df_stock.loc[i, "close"] = 99.0  # days 1-3: stable at 99
    df_stock.loc[3, "close"] = 89.0  # day 4: -10.1% triggers 10% hard stop

    with patch("backtest.get_stock_data") as mock_get:
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
    from backtest import run_factor_backtest

    base_date = datetime(2024, 1, 1)
    df_stock = _make_kline(base_date, n=35, start_price=100.0, slope=0.2)

    with patch("backtest.get_stock_data") as mock_get:
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
    from backtest import run_factor_backtest

    base_date = datetime(2024, 1, 1)
    df_stock = _make_kline(base_date, n=35, start_price=100.0, slope=0.0)

    with patch("backtest.get_stock_data") as mock_get:
        mock_get.return_value = df_stock

        result = run_factor_backtest(
            symbols=["000001"],
            days=35,
            initial_cash=1_000_000,
            initial_positions=[],
        )

    assert result is not None
    assert "trade_count" in result or "metrics" in result
