"""P0-1 修复测试：PaperBroker 双重现金账本 bug

背景：
    PaperBroker 历史上同时维护 self.cash 和 self._broker.cash 两个现金字段。
    self.cash 在 __init__ 中赋初值后**从未更新**，导致 _query_balance() 永远
    返回初始资金。本测试验证修复后 _query_balance() 会随成交而变化。

修复策略：
    - 删除 PaperBroker.__init__ 中的 self.cash = initial_cash
    - 所有 PaperBroker 内部 self.cash 引用改用 self._broker.cash
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
from lib.broker import PaperBroker
from lib.broker_data import Order


def _make_market_data(date, close=10.0, pre_close=10.0, volume=1_000_000):
    """构造最小市场数据 DataFrame（单只股票，单日）"""
    return pd.DataFrame([{
        "trade_date": pd.Timestamp(date),
        "open": close, "close": close,
        "high": close * 1.01, "low": close * 0.99,
        "volume": volume,
        "pre_close": pre_close,
        "pct_chg": 0.0,
        "is_limit_up": False,
        "is_limit_down": False,
        "is_suspend": False,
        "名称": "TEST",
    }])


def test_paper_broker_no_double_cash_ledger():
    """P0-1：PaperBroker 不应保留独立的 self.cash 字段

    修复前: pb.cash == initial_cash（永远是初始值）
    修复后: PaperBroker 不再持有 self.cash 属性（必须从 _broker 拿）
    """
    pb = PaperBroker(initial_cash=100_000)

    # 修复后不应再有 self.cash 属性（消除双重账本）
    assert not hasattr(pb, "cash"), (
        "P0-1 BUG: PaperBroker 仍持有 self.cash 属性。"
        "应删除 self.cash = initial_cash，改用 self._broker.cash 单一来源。"
    )


def test_paper_broker_query_balance_decreases_after_buy():
    """P0-1：买入后 _query_balance() 现金必须减少

    修复前: balance.cash 永远 = initial_cash（self.cash 未更新）
    修复后: balance.cash == self._broker.cash（已扣减买入成本）
    """
    initial_cash = 100_000
    pb = PaperBroker(initial_cash=initial_cash)
    pb.connect()  # 触发 _connect

    # 注入市场数据（避免触发实时数据拉取）
    trade_date = "2026-06-07"
    md = _make_market_data(trade_date, close=10.0)
    pb.update_market_data("000001", md)
    pb.set_trade_dates([trade_date])

    # 下单买入 1000 股 @ 10 元
    order = Order(
        code="000001", direction="BUY",
        price=10.0, shares=1000,
        date=trade_date, reason="P0-1 测试买入"
    )
    trade = pb.submit_order(order)

    # 必须成交
    assert trade is not None, "测试前置失败：买单未成交"
    # 成交后 _broker.cash 应减少
    assert pb._broker.cash < initial_cash, (
        f"_broker.cash 异常: 买入后现金仍为 {pb._broker.cash}"
    )

    # 关键断言：_query_balance() 报回的现金 == _broker.cash（而非初始值）
    balance = pb.get_balance()
    assert balance is not None, "_query_balance() 返回 None"
    assert "cash" in balance, f"_query_balance() 缺 cash 字段: {balance}"
    assert balance["cash"] < initial_cash, (
        f"P0-1 BUG: _query_balance() 返回现金 {balance['cash']}，"
        f"仍等于初始资金 {initial_cash}。说明 self.cash 未同步，"
        f"实际 _broker.cash={pb._broker.cash}。双重账本未消除。"
    )
    # 严格相等：cash 字段直接来自 _broker.cash
    assert balance["cash"] == pb._broker.cash, (
        f"balance.cash={balance['cash']} != _broker.cash={pb._broker.cash}。"
        f"PaperBroker._query_balance 应直接读 self._broker.cash。"
    )


def test_paper_broker_query_balance_matches_after_sell():
    """P0-1：先买入后卖出，现金变化全程正确跟踪

    验证 _query_balance() 与 _broker.cash 始终一致（不仅买入侧）
    """
    initial_cash = 100_000
    pb = PaperBroker(initial_cash=initial_cash)
    pb.connect()

    trade_date_1 = "2026-06-07"
    trade_date_2 = "2026-06-08"
    pb.set_trade_dates([trade_date_1, trade_date_2])

    # T 日买入
    md1 = _make_market_data(trade_date_1, close=10.0)
    pb.update_market_data("000001", md1)
    order_buy = Order(code="000001", direction="BUY",
                      price=10.0, shares=1000,
                      date=trade_date_1, reason="T日买入")
    trade_buy = pb.submit_order(order_buy)
    assert trade_buy is not None

    # 买入后：现金减少
    bal_after_buy = pb.get_balance()
    assert bal_after_buy["cash"] < initial_cash
    assert bal_after_buy["cash"] == pb._broker.cash

    # T+1 日卖出（高于成本价，多 5000 元利润）
    md2 = _make_market_data(trade_date_2, close=15.0, pre_close=10.0)
    pb.update_market_data("000001", md2)
    order_sell = Order(code="000001", direction="SELL",
                       price=15.0, shares=1000,
                       date=trade_date_2, reason="T+1日卖出")
    trade_sell = pb.submit_order(order_sell)
    assert trade_sell is not None

    # 卖出后：现金应 > 初始（毛利 + 5000 - 手续费 - 印花税）
    bal_after_sell = pb.get_balance()
    assert bal_after_sell["cash"] > initial_cash, (
        f"卖出盈利 5000 后现金 {bal_after_sell['cash']} 未超过初始 {initial_cash}。"
        f"实际 _broker.cash={pb._broker.cash}。"
    )
    assert bal_after_sell["cash"] == pb._broker.cash


if __name__ == "__main__":
    test_paper_broker_no_double_cash_ledger()
    print("PASS: test_paper_broker_no_double_cash_ledger")
    test_paper_broker_query_balance_decreases_after_buy()
    print("PASS: test_paper_broker_query_balance_decreases_after_buy")
    test_paper_broker_query_balance_matches_after_sell()
    print("PASS: test_paper_broker_query_balance_matches_after_sell")
