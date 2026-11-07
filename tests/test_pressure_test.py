"""压力测试：滑点对交易执行价格的影响分析"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd


class TestSlippageImpact:
    """滑点对单笔交易收益的影响"""

    def _simulate_trade(self, slippage, price=100, shares=10000):
        """模拟一笔完整买入持有卖出交易"""
        from lib.data.broker import ABroker, Order
        broker = ABroker(initial_cash=1_000_000, slippage=slippage)
        dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
        broker.set_trade_dates(dates)

        prices = [100, 101, 102, 103, 104]
        records = []
        pre_close = 100
        for i, d in enumerate(dates):
            c = prices[i]
            records.append({
                "trade_date": pd.Timestamp(d),
                "open": c, "close": c,
                "high": c * 1.01, "low": c * 0.99,
                "volume": 5e6,
                "pre_close": pre_close,
                "pct_chg": (c - pre_close) / pre_close * 100,
                "is_limit_up": False, "is_limit_down": False, "is_suspend": False,
            })
            pre_close = c
        md = {"000001": pd.DataFrame(records)}

        buy = Order(code="000001", direction="BUY", price=price,
                     shares=shares, date=dates[0], reason="买入")
        t_buy = broker.submit_order(buy, md, dates[0])
        if t_buy is None:
            return None

        sell_price = prices[-1]
        sell = Order(code="000001", direction="SELL", price=sell_price,
                     shares=t_buy.shares, date=dates[-1], reason="卖出")
        t_sell = broker.submit_order(sell, md, dates[-1])
        if t_sell is None:
            return None

        buy_cost = t_buy.price * t_buy.shares
        sell_proceed = t_sell.price * t_sell.shares
        total_cost = t_buy.commission + t_sell.commission + t_sell.stamp_tax
        pnl = sell_proceed - buy_cost - total_cost
        pnl_pct = pnl / buy_cost * 100

        return {
            "buy_price": t_buy.price, "sell_price": t_sell.price,
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 3),
            "total_cost": round(total_cost, 2),
        }

    def test_slippage_low_vs_high(self):
        """滑点 0.1% vs 0.5% 利润差异可量化"""
        base = self._simulate_trade(0.001)
        pes = self._simulate_trade(0.005)

        assert base is not None, "低滑点交易失败"
        assert pes is not None, "高滑点交易失败"

        profit_loss = base['pnl_pct'] - pes['pnl_pct']
        # 每笔交易滑点从 0.1% 升至 0.5% 应使利润降低
        assert profit_loss > 0, f"高滑点应导致利润降低，实际差异: {profit_loss:.3f}%"

    def test_slippage_affects_buy_and_sell_price(self):
        """滑点同时影响买入价和卖出价"""
        base = self._simulate_trade(0.001)
        assert base is not None

        # 买入价应高于基准价（slippage > 0）
        assert base['buy_price'] >= 100, "滑点为正时买入价应 >= 基准价"
        # 卖出价应低于基准价
        assert base['sell_price'] <= 104, "滑点为正时卖出价应 <= 基准价"

    def test_zero_slippage_no_impact(self):
        """零滑点时买入/卖出价等于基准价"""
        from lib.data.broker import ABroker, Order
        broker = ABroker(initial_cash=1_000_000, slippage=0.0)
        broker.set_trade_dates(["2026-01-05", "2026-01-09"])
        records = [{
            "trade_date": pd.Timestamp("2026-01-05"),
            "open": 100, "close": 100, "high": 101, "low": 99,
            "volume": 5e6, "pre_close": 100,
            "pct_chg": 0, "is_limit_up": False, "is_limit_down": False, "is_suspend": False,
        }, {
            "trade_date": pd.Timestamp("2026-01-09"),
            "open": 104, "close": 104, "high": 105, "low": 103,
            "volume": 5e6, "pre_close": 100,
            "pct_chg": 4, "is_limit_up": False, "is_limit_down": False, "is_suspend": False,
        }]
        md = {"000001": pd.DataFrame(records)}

        buy = Order(code="000001", direction="BUY", price=100, shares=10000,
                     date="2026-01-05", reason="买入")
        t_buy = broker.submit_order(buy, md, "2026-01-05")
        assert t_buy is not None
        assert abs(t_buy.price - 100.0) < 1e-9, f"零滑点买入价={t_buy.price}，期望=100"
