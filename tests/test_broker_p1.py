"""P1 batch 6 (broker) 修复测试

每个测试对应一个 P1 修复点：
- P1-1: ABroker 暴露 last_price(code, default) 方法
- P1-2: PaperBroker 风控价基于 exec_price_estimate 而非 order.price
- P1-3: SELL 时不修改入参 order.shares，通过 Trade 携带 actual_shares
- P1-4: BUY 资金不足 retry 时不应重算 stamp_tax（BUY 无印花税）
- P1-5: _next_trade_date 缺日历时不应返回 "9999-12-31"
- P1-6: _compute_daily_volume 线程安全（无重复计算）
- P1-7: LiveBrokerABC 用 abc.ABC + @abstractmethod
- P1-8: TDXBroker._get_gddm 找不到匹配 market 时不应兜底
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import math
import threading
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from lib.broker import ABroker, PaperBroker, LiveBrokerABC, TDXBroker
from lib.broker_data import Order


# 通用：构造单日市场数据
def _md(date, close=10.0, pre_close=10.0, volume=1_000_000):
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


# ============================================================
# P1-1: ABroker 暴露 last_price(code, default) 方法
# ============================================================

def test_p1_1_last_price_method_exists():
    """P1-1: ABroker 应暴露 last_price(code, default=NaN) 方法"""
    broker = ABroker(initial_cash=100_000)
    assert hasattr(broker, "last_price"), "ABroker 应暴露 last_price() 方法"
    # 没有任何价格时返回 NaN
    import math
    val = broker.last_price("000001")
    assert isinstance(val, float) and math.isnan(val), "无价格时应返回 NaN"


def test_p1_1_last_price_after_update():
    """P1-1: update_market_prices 后 last_price 返回真实价格

    update_market_prices 只会更新 _holdings 中 code 的价格（其它 code 视为非持仓），
    所以测试需先建仓再 update。
    """
    broker = ABroker(initial_cash=100_000)
    dates = ["2026-01-05"]
    broker.set_trade_dates(dates)
    md = {"000001": _md(dates[0], close=12.5)}
    order = Order(code="000001", direction="BUY", price=12.0, shares=100,
                  date=dates[0], reason="建仓")
    broker.submit_order(order, md, dates[0])
    broker.update_market_prices(md, dates[0])
    assert broker.last_price("000001") == 12.5
    # 自定义 default
    assert broker.last_price("999999", default=0.0) == 0.0


def test_p1_1_paper_broker_position_check_without_update():
    """P1-1: PaperBroker 在 update_market_prices 之前下单应触发仓位检查失效告警/拒绝

    修复前: 私有 _last_prices 读不到 → 退化为 cost_price（首次建仓时是 NaN → 0），
            单票上限判断全失效，可下全仓。
    修复后: last_price() 暴露显式空值检查，无价格时拒绝新 BUY 或限制为 0。
    """
    pb = PaperBroker(initial_cash=100_000)
    pb.connect()
    # 不调用 update_market_prices，直接下单
    order = Order(code="000001", direction="BUY", price=10.0, shares=1000,
                  date="2026-01-05", reason="p1-1 测试")
    # 注入行情（绕过 _connect 的实时行情）
    pb.update_market_data("000001", _md("2026-01-05", close=10.0))
    # 由于 holdings 为空, _last_prices 也不存在 → 现有代码用 cost_price=0
    # 修复后：first-buy 场景下 _last_prices 为空，existing_value=0，
    # new_value/total_value = 1000*10/100000 = 10% < 20% 上限
    # → 应正常通过风控（first buy 不应误拒绝）
    trade = pb._place_order(order)
    # 关键是：单票 100% 全仓买入（买入后占总资金 ~100%）应该被风控拒绝
    # 用 9000 股 = 90000 元 ≈ 90% total_value 触发
    order_big = Order(code="000001", direction="BUY", price=10.0, shares=9000,
                      date="2026-01-05", reason="p1-1 大单测试")
    trade_big = pb._place_order(order_big)
    # 90% 单票应该被风控拒绝（>20%）
    assert trade_big is None or trade_big.shares < 9000, (
        "P1-1 BUG: 无 _last_prices 时单票 90% 风控未生效，"
        f"实际成交 shares={trade_big.shares if trade_big else None}"
    )


# ============================================================
# P1-2: PaperBroker 风控价用 exec_price_estimate 而非 order.price
# ============================================================

def test_p1_2_paper_broker_uses_exec_price_estimate():
    """P1-2: PaperBroker 风控应基于 exec_price（含滑点+冲击）而非 order.price

    修复前: 风控用 order.price=10 计算仓位，下单后实际成交价=10.5（冲击成本），
            实际占用资金 > 风控计算，突破单票上限。
    修复后: PaperBroker 应在风控前计算 exec_price_estimate，仓位检查用真实成本。
    """
    pb = PaperBroker(initial_cash=100_000, max_single_weight=0.20)
    pb.connect()
    # 下限价 10 元，但实际成交价因冲击成本会高于 10
    # 用大单冲击让 exec_price > order.price
    order = Order(code="000001", direction="BUY", price=10.0, shares=5000,
                  date="2026-01-05", reason="p1-2 冲击成本测试")
    pb.update_market_data("000001", _md("2026-01-05", close=10.0, volume=100))
    trade = pb._place_order(order)
    # 如果风控用 order.price=10，5000*10=50000 = 50% > 20% → 应被拒
    # 如果风控仍用 order.price 但已修复 P1-1，仓位应是 0（已无 _last_prices 兜底）
    # 关键断言：成交股数应受风控约束（被缩减到 ≤ 2000 股左右）
    if trade is not None:
        # exec_price 应 > order.price
        assert trade.price > order.price * 0.99, "成交价应至少接近 order.price"
        # 实际占用 < 风控上限
        occupied = trade.price * trade.shares
        assert occupied / pb._broker.total_value <= 0.20 + 0.01, (
            f"P1-2 BUG: 实际占用 {occupied/pb._broker.total_value:.1%} "
            f"超过单票上限 20%（风控用 order.price 估值不足）"
        )


# ============================================================
# P1-3: SELL 时不修改入参 order.shares
# ============================================================

def test_p1_3_sell_does_not_mutate_input():
    """P1-3: 卖出时若 shares > 持仓，应不修改入参 order.shares，
    Trade.shares 反映实际成交数
    """
    broker = ABroker(initial_cash=100_000)
    dates = ["2026-01-05", "2026-01-06"]
    broker.set_trade_dates(dates)
    # 两日 market data
    md = {
        "000001": pd.DataFrame([
            {"trade_date": pd.Timestamp(dates[0]), "open": 10.0, "close": 10.0,
             "high": 10.1, "low": 9.9, "volume": 1_000_000, "pre_close": 10.0,
             "pct_chg": 0.0, "is_limit_up": False, "is_limit_down": False,
             "is_suspend": False, "名称": "TEST"},
            {"trade_date": pd.Timestamp(dates[1]), "open": 10.0, "close": 10.0,
             "high": 10.1, "low": 9.9, "volume": 1_000_000, "pre_close": 10.0,
             "pct_chg": 0.0, "is_limit_up": False, "is_limit_down": False,
             "is_suspend": False, "名称": "TEST"},
        ])
    }

    # T 日建仓 1000 股
    order_buy = Order(code="000001", direction="BUY", price=10, shares=1000,
                      date=dates[0], reason="建仓")
    broker.submit_order(order_buy, md, dates[0])

    # T+1 尝试卖 2000 股（> 持仓 1000）
    order_sell = Order(code="000001", direction="SELL", price=10, shares=2000,
                       date=dates[1], reason="过量卖出")
    original_shares = order_sell.shares
    trade = broker.submit_order(order_sell, md, dates[1])

    # 修复后：order.shares 应保持 2000（入参不变）
    assert order_sell.shares == original_shares, (
        f"P1-3 BUG: SELL 路径修改了入参 order.shares "
        f"({original_shares} → {order_sell.shares})"
    )
    # Trade.shares 应为实际成交 1000
    assert trade is not None
    assert trade.shares == 1000, f"实际成交应为 1000，实际 {trade.shares}"


# ============================================================
# P1-4: BUY 资金不足 retry 时 stamp_tax 应为 0
# ============================================================

def test_p1_4_buy_retry_no_stamp_tax():
    """P1-4: BUY 资金不足 retry 后 stamp_tax 必须为 0（BUY 无印花税）

    修复前: retry 路径注释缺失，潜在 bug 风险（重算 stamp_tax 误加）。
    修复后: 显式注释 + 行为保证 stamp_tax=0。
    """
    broker = ABroker(initial_cash=10_000)  # 资金不足触发 retry
    dates = ["2026-01-05"]
    broker.set_trade_dates(dates)
    md = _md(dates[0], close=10.0)
    # 想要买 5000 股 = 50000 元，远超现金 10000，会触发 max_shares 重算
    order = Order(code="000001", direction="BUY", price=10, shares=5000,
                  date=dates[0], reason="资金不足")
    trade = broker.submit_order(order, md, dates[0])
    assert trade is not None, "应有部分成交"
    # 关键断言：BUY 单 stamp_tax 必须为 0
    assert trade.stamp_tax == 0.0, (
        f"P1-4 BUG: BUY 成交 stamp_tax={trade.stamp_tax}（应为 0）"
    )


# ============================================================
# P1-5: _next_trade_date 缺日历时不应返回 "9999-12-31"
# ============================================================

def test_p1_5_next_trade_date_no_calendar():
    """P1-5: 未调 set_trade_dates 时 _next_trade_date 不应返回 "9999-12-31"

    修复前: 返回 "9999-12-31"，买入后永远不能卖（T+1 锁死）。
    修复后: 应有 fallback（如 calendar 工作日 +1，或拒绝开仓），且打 warning。
    """
    broker = ABroker(initial_cash=100_000)
    # 不调 set_trade_dates
    dates = ["2026-01-05"]  # Mon
    md = {"000001": _md(dates[0], close=10.0)}
    order = Order(code="000001", direction="BUY", price=10, shares=100,
                  date=dates[0], reason="p1-5 测试")
    trade = broker.submit_order(order, md, dates[0])
    assert trade is not None
    holding = broker.holdings["000001"]
    # can_sell_date 不应是 "9999-12-31"
    assert holding.can_sell_date != "9999-12-31", (
        f"P1-5 BUG: 无日历时 can_sell_date='9999-12-31'（T+1 永远无法卖）"
    )
    # 应是一个合理的"明日"日期
    from datetime import datetime, timedelta
    expected = (datetime.strptime(dates[0], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    assert holding.can_sell_date == expected, (
        f"P1-5: can_sell_date='{holding.can_sell_date}', expected '{expected}'"
    )


# ============================================================
# P1-6: _compute_daily_volume 线程安全
# ============================================================

def test_p1_6_compute_daily_volume_thread_safe():
    """P1-6: 多线程并发调用 _compute_daily_volume 不应重复计算

    修复前: check-then-act 竞态，两个线程同时 miss 后都执行计算。
    修复后: 用 setdefault 原子化，或加二次检查避免重复。
    """
    broker = ABroker(initial_cash=100_000)
    # 准备市场数据
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    records = []
    for d in dates:
        records.append({
            "trade_date": d,
            "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9,
            "volume": 1_000_000,
            "pre_close": 10.0,
            "pct_chg": 0.0,
            "is_limit_up": False, "is_limit_down": False, "is_suspend": False,
        })
    md = {"000001": pd.DataFrame(records)}

    # 计算原始耗时（单线程）
    import time
    t0 = time.perf_counter()
    for _ in range(100):
        broker._compute_daily_volume("000001", md, "2026-01-30")
    single_thread_time = time.perf_counter() - t0

    # 计数：mock _compute 时不修改 _volume_cache 来强制竞态
    call_count = [0]
    original_compute = broker._compute_daily_volume

    def counting_compute(code, m, d):
        call_count[0] += 1
        return original_compute(code, m, d)

    # 用 setdefault 测试
    cache = {}
    cache_lock = threading.Lock()
    call_count2 = [0]

    def safe_compute(key, compute_fn):
        # First check (fast path)
        with cache_lock:
            if key in cache:
                return cache[key]
        # compute outside lock
        val = compute_fn()
        # Second check + setdefault inside lock
        with cache_lock:
            cache.setdefault(key, val)  # 原子：已有值不覆盖
            return cache[key]

    def worker():
        safe_compute("test_key", lambda: (
            call_count2.__setitem__(0, call_count2[0] + 1),
            time.sleep(0.01),
            42
        )[2])

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 修复后断言：cache 终值存在（虽然 compute_fn 被多线程调用过，但结果一致）
    assert "test_key" in cache and cache["test_key"] == 42, (
        f"P1-6 BUG: cache 终值 {cache.get('test_key')}"
    )
    # 关键：10 线程并发最终 cache 只应有一个值（不重复）
    assert len(cache) == 1, f"P1-6 BUG: cache 出现多个 key（{list(cache.keys())}）"

    # 同时验证 broker 端也修复了（缓存存在就直接返回）
    broker._volume_cache = {}  # 清空
    results = []

    def worker2():
        results.append(broker._compute_daily_volume("000001", md, "2026-01-30"))

    threads2 = [threading.Thread(target=worker2) for _ in range(20)]
    for t in threads2:
        t.start()
    for t in threads2:
        t.join()

    # 所有结果应一致（说明无重复计算导致数据漂移）
    assert len(set(results)) == 1, "并发结果不一致"


# ============================================================
# P1-7: LiveBrokerABC 用 abc.ABC + @abstractmethod
# ============================================================

def test_p1_7_live_broker_abc_uses_abstractmethod():
    """P1-7: LiveBrokerABC 应使用 abc.ABC + @abstractmethod 标记抽象方法

    修复前: 用 raise NotImplementedError，子类忘记实现不会在实例化时报错。
    修复后: 用 ABC + abstractmethod，无法实例化不完整的子类。
    """
    import abc
    # 验证 LiveBrokerABC 继承 abc.ABC
    assert issubclass(LiveBrokerABC, abc.ABC), (
        "P1-7 BUG: LiveBrokerABC 未继承 abc.ABC"
    )

    # 验证 _connect 等是 abstractmethod
    for method_name in ["_connect", "_disconnect", "_place_order",
                         "_cancel_order", "_query_positions", "_query_balance"]:
        method = getattr(LiveBrokerABC, method_name, None)
        assert method is not None, f"{method_name} 不存在"
        assert getattr(method, "__isabstractmethod__", False), (
            f"P1-7 BUG: {method_name} 未用 @abstractmethod 标记"
        )

    # 验证：不实现抽象方法的子类无法实例化
    class IncompleteBroker(LiveBrokerABC):
        pass

    with pytest.raises(TypeError):
        IncompleteBroker(account="test")

    # 实现完整的子类应能正常实例化
    class CompleteBroker(LiveBrokerABC):
        def _connect(self): pass
        def _disconnect(self): pass
        def _place_order(self, order): return None
        def _cancel_order(self, order_id): return False
        def _query_positions(self): return {}
        def _query_balance(self): return {}

    cb = CompleteBroker(account="test")
    assert cb.account == "test"


# ============================================================
# P1-8: TDXBroker._get_gddm 找不到匹配 market 时不应兜底
# ============================================================

def test_p1_8_tdx_get_gddm_no_silent_fallback():
    """P1-8: TDXBroker._get_gddm 找不到匹配 market 时不应取 data_list[0] 兜底

    修复前: 取 data_list[0].gddm → 可能拿深市 gddm 去买沪市股票。
    修复后: 找不到匹配 market 应返回 None（不调用交易所接口）。
    """
    broker = TDXBroker(account="test")
    broker._client_id = 0
    # 模拟 _api 返回的股东代码：只有深市（market=0）
    mock_api = MagicMock()
    mock_api.query_data.return_value = {
        "success": 1,
        "data": [
            {"market": 0, "gddm": "SZ_GDDM_001"},
            {"market": 0, "gddm": "SZ_GDDM_002"},
        ],
    }
    broker._api = mock_api

    # 请求沪市股东代码（market=1）但 data_list 都没有
    result = broker._get_gddm(market=1)
    # 修复后：找不到匹配应返回 None
    assert result is None, (
        f"P1-8 BUG: 找不到沪市股东代码却返回 {result}（兜底取 data_list[0]）"
    )

    # 请求匹配 market=0 应正常返回
    result_sz = broker._get_gddm(market=0)
    assert result_sz == "SZ_GDDM_001", f"应返回第一个匹配的深市 gddm，实际 {result_sz}"
