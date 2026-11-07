"""A股回测引擎核心规则测试：T+1 / 涨停 / 整手 / 停牌"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pandas as pd
from lib.data.broker import ABroker  # ABroker 在 lib/broker.py 中
from lib.risk import Order, TradingConstraints# 从子包 lib/broker_data/ 导入


def _make_market_data(dates, close_vec, limit_up=False, limit_down=False,
                      suspend=False, code="000001"):
    """构造模拟市场数据"""
    records = []
    pre_close = close_vec[0]
    for i, d in enumerate(dates):
        c = close_vec[i]
        pct = (c - pre_close) / pre_close * 100 if pre_close > 0 else 0
        records.append({
            "trade_date": pd.Timestamp(d),
            "open": c, "close": c, "high": c * 1.01, "low": c * 0.99,
            "volume": 1e6 if not suspend else 0,
            "pre_close": pre_close,
            "pct_chg": pct,
            "is_limit_up": limit_up,
            "is_limit_down": limit_down,
            "is_suspend": suspend,
        })
        pre_close = c
    df = pd.DataFrame(records)
    return {code: df}


def test_t_plus_1():
    """T日买入 → T日不可卖出 → T+1日可卖出"""
    broker = ABroker(initial_cash=1_000_000)
    dates = ["2026-01-05", "2026-01-06"]  # Mon, Tue
    broker.set_trade_dates(dates)
    md = _make_market_data(dates, [100, 102])

    # T日买入
    order = Order(code="000001", direction="BUY", price=100, shares=1000,
                  date=dates[0], reason="测试买入")
    trade = broker.submit_order(order, md, dates[0])
    assert trade is not None, "T日买入失败"
    assert "000001" in broker.holdings, "持仓未记录"

    # T日卖出（应被 T+1 拒绝）
    order_sell = Order(code="000001", direction="SELL", price=102, shares=1000,
                       date=dates[0], reason="测试卖出")
    trade_sell = broker.submit_order(order_sell, md, dates[0])
    assert trade_sell is None, "T+1 违规：T日应不可卖出"

    # T+1日卖出（应成功）
    order_sell2 = Order(code="000001", direction="SELL", price=102, shares=1000,
                        date=dates[1], reason="测试卖出")
    trade_sell2 = broker.submit_order(order_sell2, md, dates[1])
    assert trade_sell2 is not None, "T+1日卖出失败"


def test_limit_up_rejects_buy():
    """涨停日买入订单应被拒绝"""
    broker = ABroker(initial_cash=1_000_000)
    dates = ["2026-01-05"]
    broker.set_trade_dates(dates)
    md = _make_market_data(dates, [100], limit_up=True)

    order = Order(code="000001", direction="BUY", price=100, shares=1000,
                  date=dates[0], reason="涨停买入")
    trade = broker.submit_order(order, md, dates[0])
    assert trade is None, "涨停日应拒绝买入"


def test_limit_down_rejects_sell():
    """跌停日卖出订单应被拒绝"""
    broker = ABroker(initial_cash=1_000_000)
    dates = ["2026-01-05"]
    broker.set_trade_dates(dates)
    md = _make_market_data(dates, [100], limit_down=True)

    # 先买入持仓
    md_buy = _make_market_data(dates, [100])
    order_buy = Order(code="000001", direction="BUY", price=100, shares=1000,
                      date=dates[0], reason="先行买入")
    broker.submit_order(order_buy, md_buy, dates[0])

    # 跌停日卖出
    order_sell = Order(code="000001", direction="SELL", price=100, shares=1000,
                       date=dates[0], reason="跌停卖出")
    trade = broker.submit_order(order_sell, md, dates[0])
    assert trade is None, "跌停日应拒绝卖出"


def test_lot_size_rounding():
    """买入量自动向下取整到整手(100)"""
    broker = ABroker(initial_cash=1_000_000)
    dates = ["2026-01-05"]
    broker.set_trade_dates(dates)
    md = _make_market_data(dates, [100])

    # 非整手数
    order = Order(code="000001", direction="BUY", price=100, shares=1050,
                  date=dates[0], reason="非整手")
    trade = broker.submit_order(order, md, dates[0])
    assert trade is not None
    # 应为 1000 而不是 1050
    assert trade.shares % 100 == 0, f"股数 {trade.shares} 不是整手"


def test_suspend_rejects_buy():
    """停牌日买入订单应被拒绝"""
    broker = ABroker(initial_cash=1_000_000)
    dates = ["2026-01-05"]
    broker.set_trade_dates(dates)
    md = _make_market_data(dates, [100], suspend=True)

    order = Order(code="000001", direction="BUY", price=100, shares=1000,
                  date=dates[0], reason="停牌买入")
    trade = broker.submit_order(order, md, dates[0])
    assert trade is None, "停牌日应拒绝买入"


def test_suspend_rejects_sell():
    """停牌日卖出订单应被拒绝"""
    broker = ABroker(initial_cash=1_000_000)
    dates = ["2026-01-05", "2026-01-06"]
    broker.set_trade_dates(dates)
    # T日正常买入
    md_normal = _make_market_data(dates[:1], [100])
    order = Order(code="000001", direction="BUY", price=100, shares=1000,
                  date=dates[0], reason="先行买入")
    broker.submit_order(order, md_normal, dates[0])

    # T+1日停牌，尝试卖出
    md_suspend = _make_market_data(dates, [100, 100], suspend=True)
    order_sell = Order(code="000001", direction="SELL", price=100, shares=1000,
                       date=dates[1], reason="停牌卖出")
    trade = broker.submit_order(order_sell, md_suspend, dates[1])
    assert trade is None, "停牌日应拒绝卖出"


def test_tdx_broker_holdings_no_attribute_error():
    """P0-2: TDXBroker.holdings 旧版引用不存在的 self._broker，应删除该 property

    旧实现：@property holdings 返回 self._broker.holdings，
    但 TDXBroker.__init__ 没有 self._broker = ABroker(...)，
    任何访问 broker.holdings 的代码都会抛 AttributeError。
    修复：删除该 property，TDXBroker 走 _query_positions 路径查询实盘持仓。
    """
    from lib.data.broker import TDXBroker
    broker = TDXBroker(account="test_account")
    # 修复后（推荐方案）：完全删除 holdings property
    # 调用方应改用 broker.get_positions() 走 _query_positions() 路径
    # 任何对 broker.holdings 的访问应抛 AttributeError
    # 但**绝不能**包含 "_broker" 字样（证明旧引用已删除）
    try:
        result = broker.holdings
        # 另一种合法修复：返回 dict/None（get_positions 风格）
        assert isinstance(result, (dict, type(None))), \
            f"TDXBroker.holdings 应为 dict 或 None，得到 {type(result)}"
    except AttributeError as e:
        msg = str(e)
        assert "_broker" not in msg, (
            f"P0-2 未修复：TDXBroker.holdings 仍引用不存在的 self._broker: {msg}"
        )
        # AttributeError 但不包含 '_broker' → 证明 property 已删除，是预期行为
        assert "holdings" in msg, f"AttributeError 消息应提及 holdings: {msg}"


def test_get_market_cap_no_lookahead():
    """P0-1: _get_market_cap 不应调用 ak.stock_individual_info_em（未来函数）

    旧实现调用 akshare 拿**当前实时**流通股本，回测 2019 时用 close × 实时股本
    = 错误市值（数据穿越）。
    修复：完全回退到 volume 估算路径（sentinel = -2），不走 akshare 实时接口。
    """
    import lib.data.broker as broker_mod

    call_count = {"n": 0}
    original_func = broker_mod.ak.stock_individual_info_em

    def tracking_call(symbol):
        call_count["n"] += 1
        return original_func(symbol)  # 实际不会调用，只为了类型兼容

    broker_mod.ak.stock_individual_info_em = tracking_call

    try:
        broker = ABroker(initial_cash=1_000_000)
        dates = ["2024-01-05"]
        # volume > 0 时应走估算路径（sentinel=-2）
        md = _make_market_data(dates, [100])
        cap = broker._get_market_cap("000001", md, dates[0])
        assert call_count["n"] == 0, (
            f"P0-1 未修复：_get_market_cap 仍调用 ak.stock_individual_info_em "
            f"({call_count['n']} 次)，回测时会造成未来函数"
        )
        assert cap == -2, (
            f"P0-1 未修复：应回退到 volume 估算路径（sentinel=-2），实际={cap}"
        )
    finally:
        broker_mod.ak.stock_individual_info_em = original_func


def test_delisted_holdings_cleared_after_60_days():
    """P0-3: 退市后 holdings 应被清理，submit_order 拒绝 delisted code

    旧实现：60 天无行情仅写入 _delisted_codes，但 holdings 仍按 _missing_days
    路径追踪"0 价"，净值仍计入（成本价退路兜底），update_market_prices 后 60 天内
    仍占用 position_count 触发策略调仓信号。
    修复：
    1. update_market_prices 标记 delisted 时，从 holdings 中强制移除该 code
    2. submit_order 拒绝 SELL 已退市 code（避免幽灵持仓被减仓）
    """
    broker = ABroker(initial_cash=1_000_000)
    dates = ["2024-01-02", "2024-01-03"]
    broker.set_trade_dates(dates)
    md_buy = _make_market_data(dates[:1], [100])
    order = Order(code="000001", direction="BUY", price=100, shares=1000,
                  date=dates[0], reason="买入即将退市股")
    trade = broker.submit_order(order, md_buy, dates[0])
    assert trade is not None
    assert "000001" in broker.holdings

    # 模拟 60 天无行情（无 market_data）
    empty_md: dict = {}
    for i in range(60):
        broker.update_market_prices(empty_md, dates[0])

    # 修复后：holdings 应被强制清理
    assert "000001" not in broker.holdings, (
        f"P0-3 未修复：60 天无行情后 holdings 仍含退市股 "
        f"(holdings={list(broker.holdings.keys())}, "
        f"delisted={broker.get_delisted_codes()})"
    )
    # position_count 同步减 1
    assert broker.position_count == 0, (
        f"P0-3 未修复：position_count={broker.position_count}，应清理退市股后归零"
    )

    # 修复后：submit_order 应拒绝 delisted code
    order_sell = Order(code="000001", direction="SELL", price=100, shares=1000,
                       date=dates[1], reason="卖出已退市股")
    trade_sell = broker.submit_order(order_sell, md_buy, dates[1])
    assert trade_sell is None, (
        "P0-3 未修复：submit_order 仍接受退市 code 的 SELL 订单"
    )


def test_paper_broker_uses_internal_lot_size():
    """P0-4: PaperBroker 风控缩量应使用 self._broker.lot_size 而非硬编码 100

    旧实现 L796/L807 用 int(... / 100) * 100 硬编码 100，
    与 self._broker.lot_size（默认 100，可被覆写为其他值）解耦。
    当内部 ABroker 的 lot_size 被设为非 100 时：
    - PaperBroker 缩量后 order.shares = 100 * K（K 整数）
    - ABroker.submit_order 再用 self.lot_size=L floor
    - 若 L > order.shares（缩量结果 < lot_size），floor → 0，订单被静默丢弃

    修复：PaperBroker 风控缩量时使用 self._broker.lot_size 统一口径，
    避免两次缩量基线不一致 + 缩量结果 < lot_size 时直接 return None。

    触发场景：max_single_weight 较紧，缩量结果在 [100, lot_size) 之间。
    修复后行为：PaperBroker 在缩量阶段就 floor 到 0，return None（不调 ABroker）。
    """
    from lib.data.broker import PaperBroker
    broker = PaperBroker(
        account="test", initial_cash=1_000_000,
        max_single_weight=0.01,
    )
    broker._broker.lot_size = 200
    dates = ["2024-01-02"]
    broker.set_trade_dates(dates)
    md = _make_market_data(dates, [100], code="000001")
    broker.update_market_data("000001", md["000001"])
    broker._connected = True

    order = Order(code="000001", direction="BUY", price=100, shares=10000,
                  date=dates[0], reason="紧限+非标 lot_size 测试")

    # Spy：捕获 PaperBroker 传给 ABroker 的 order.shares
    captured_shares = []
    original_submit = broker._broker.submit_order

    def spy_submit(o, md_, date_):
        captured_shares.append(o.shares)
        return original_submit(o, md_, date_)

    broker._broker.submit_order = spy_submit
    try:
        result = broker._place_order(order)
    finally:
        broker._broker.submit_order = original_submit

    # 修复后行为有两种合法路径：
    # A. PaperBroker 缩量后 order.shares > 0（multiple of lot_size=200）→ 调 ABroker
    # B. PaperBroker 缩量后 order.shares <= 0 → 直接 return None，不调 ABroker
    # 关键断言：绝对不能是"100 倍数（硬编码 100）传给 ABroker"（旧 bug）
    if captured_shares:
        floor_shares = captured_shares[0]
        assert floor_shares % 200 == 0, (
            f"P0-4 未修复：PaperBroker 缩量后 order.shares={floor_shares} "
            f"是 100 倍数（硬编码 100），不是 lot_size=200 倍数"
        )
    else:
        # PaperBroker 提前 return None（缩量=0 路径）
        assert result is None, (
            f"P0-4 异常：PaperBroker 未调 ABroker 但 result={result}（应 None）"
        )


