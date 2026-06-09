"""D5.3 — PaperBroker 私有 helper 方法测试 (2026-06-09)

D5.1 拆分后, 3 个 helper 方法可独立测:
- _check_position_limits: 风控仓位检查 (单票 + 总仓位, 必要时缩量)
- _ensure_market_data: market_data 回填 (无则拉 5 天)
- _exec_price_estimate: 成交价估算 (P1-2 修复, 默认 = order.price)

之前 P0/P1-4 隐式覆盖这些方法, 显式测试让回归更易诊断。
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from lib.broker import PaperBroker, Order
from lib.broker_data.datatypes import Holding


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def paper_broker():
    """PaperBroker 实例, 无需 _connected 也能测 helper"""
    return PaperBroker(initial_cash=1_000_000)


# ─────────────────────────────────────────────────────────────
# _check_position_limits (D5.1 拆分)
# ─────────────────────────────────────────────────────────────

def test_check_position_limits_sell_skips_check(paper_broker):
    """SELL 不受仓位约束 → 永远 True"""
    sell_order = Order(code="600519", direction="SELL", price=10.0,
                       shares=100, date="2026-06-09")
    assert paper_broker._check_position_limits(sell_order, 10.0, 100) is True


def test_check_position_limits_zero_total_value_skips(paper_broker):
    """total_value <= 0 (无账户) → 跳过检查返 True (避免除 0)"""
    # total_value 是 property, mock 它的 underlying components
    # 用 mock 整个 _broker 对象 (绕过 property)
    mock_broker = MagicMock()
    mock_broker.total_value = 0
    with patch.object(paper_broker, "_broker", mock_broker):
        buy_order = Order(code="600519", direction="BUY", price=10.0,
                          shares=100, date="2026-06-09")
        result = paper_broker._check_position_limits(buy_order, 10.0, 100)
    assert result is True


def test_check_position_limits_normal_buy_passes(paper_broker):
    """正常 BUY 不超单票上限 → True, order.shares 不变"""
    buy_order = Order(code="600519", direction="BUY", price=10.0,
                      shares=10, date="2026-06-09")
    # 1000000 cash, 10 元/股, 10 股 = 100 元, 远低于 20% 上限 (200000)
    result = paper_broker._check_position_limits(buy_order, 10.0, 100)
    assert result is True
    assert buy_order.shares == 10, "正常 BUY 不应缩量"


def test_check_position_limits_exceeds_single_shrinks(paper_broker):
    """BUY 超单票上限 → order.shares 缩量到限内, 返 True"""
    # 单股上限默认 0.20, total_value 1000000 → max_single = 200000
    # price=10, shares=30000 (300000 元) → 30% 超 20%
    buy_order = Order(code="600519", direction="BUY", price=10.0,
                      shares=30000, date="2026-06-09")
    result = paper_broker._check_position_limits(buy_order, 10.0, 100)
    assert result is True
    # 缩量: (200000) / 10 / 100 = 200 手 (20000 股)
    assert buy_order.shares == 20000, f"应缩到 20000 股, 实际 {buy_order.shares}"


def test_check_position_limits_existing_huge_blocks(paper_broker):
    """已有 huge holding 占满单票上限 → 新 BUY 缩量 max_new_value <= 0 → 返 False"""
    # mock existing holding 占 50% (超 20% 上限)
    big_holding = Holding(
        code="600519", shares=50000, cost_price=10.0,
        buy_date="2026-01-01", can_sell_date="2026-01-02",
    )
    # 用 mock 整个 _broker 绕开 property 限制
    mock_broker = MagicMock()
    mock_broker.holdings = {"600519": big_holding}
    mock_broker.last_price.return_value = 10.0
    mock_broker.total_value = 1_000_000
    with patch.object(paper_broker, "_broker", mock_broker):
        # existing_value = 50000 * 10 = 500000
        # max_single = 200000, max_new_value = 200000 - 500000 = -300000 <= 0
        buy_order = Order(code="600519", direction="BUY", price=10.0,
                          shares=100, date="2026-06-09")
        result = paper_broker._check_position_limits(buy_order, 10.0, 100)
    assert result is False, f"已有 huge holding 时 BUY 应阻断, 实际 {result}"


# ─────────────────────────────────────────────────────────────
# _ensure_market_data (D5.1 拆分)
# ─────────────────────────────────────────────────────────────

def test_ensure_market_data_already_loaded_skips(paper_broker):
    """_market_data 已有 code → 跳过拉取"""
    paper_broker._market_data["600519"] = pd.DataFrame({"close": [10.0]})
    # 拉取函数不应被调
    with patch("lib.data_layer.get_stock_hist_multi_source") as mock_fetch:
        paper_broker._ensure_market_data("600519")
    mock_fetch.assert_not_called()


def test_ensure_market_data_empty_triggers_fetch(paper_broker):
    """_market_data 无 code → 拉 5 天, 写入 _market_data"""
    fake_df = pd.DataFrame({"date": ["2026-06-01", "2026-06-09"], "close": [10.0, 11.0]})
    with patch("lib.data_layer.get_stock_hist_multi_source", return_value=fake_df) as mock_fetch:
        paper_broker._ensure_market_data("600519")
    assert "600519" in paper_broker._market_data
    assert "trade_date" in paper_broker._market_data["600519"].columns
    mock_fetch.assert_called_once_with("600519", days=5, source="auto")


def test_ensure_market_data_fetch_failure_silent(paper_broker):
    """data_layer 拉取失败 → 静默, _market_data 仍空 (不 raise)"""
    with patch("lib.data_layer.get_stock_hist_multi_source",
               side_effect=Exception("network down")):
        paper_broker._ensure_market_data("600519")
    assert "600519" not in paper_broker._market_data


# ─────────────────────────────────────────────────────────────
# _exec_price_estimate (P1-2 修复)
# ─────────────────────────────────────────────────────────────

def test_exec_price_estimate_default_returns_order_price(paper_broker):
    """默认实现 (保守): 返 order.price (不开滑点冲击)"""
    order = Order(code="600519", direction="BUY", price=10.0, shares=100, date="2026-06-09")
    market_data = {"000300": pd.DataFrame({"close": [10.0]})}
    result = paper_broker._exec_price_estimate(order, market_data, "2026-06-09")
    assert result == 10.0


def test_exec_price_estimate_can_be_overridden_by_subclass(paper_broker):
    """子类可 override 引入真实滑点模型 — 钩子存在性测试"""
    # 通过 monkey-patch 模拟子类 override
    def custom_estimate(order, market_data, date):
        return order.price * 1.01  # 1% 滑点
    paper_broker._exec_price_estimate = custom_estimate

    order = Order(code="600519", direction="BUY", price=10.0, shares=100, date="2026-06-09")
    result = paper_broker._exec_price_estimate(order, {}, "2026-06-09")
    assert result == pytest.approx(10.10), f"应返 1% 滑点 (=10.10), 实际 {result}"
