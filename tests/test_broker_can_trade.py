"""ABroker.can_trade 涨/跌停/停牌约束测试 (T7, 2026-06-09)

D5 "_check_sell_constraints 抽取" 任务实际状态:
- 误判: 假设 PaperBroker._place_order 内有 SELL 特殊分支
- 实际: SELL 约束 ("跌停无法卖出") 已在 ABroker.can_trade (line 286-287)
- 之前 0 测试覆盖, P1-2 修复隐式保证但无显式测试

本测试补 can_trade 4 状态: 无行情 / 停牌 / 涨停 / 跌停 / 正常。
covers paper trading 关键 SELL 约束 (跌停无法卖出)。
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import pandas as pd
import pytest

from lib.data.broker import ABroker


def _make_market_data(code: str = "600519", date: str = "2026-06-09",
                      **flags) -> dict:
    """构造测试用 market_data dict, flags 控制 is_suspend/is_limit_up/is_limit_down"""
    row = {"close": 10.0}
    row.update(flags)
    df = pd.DataFrame({"trade_date": [pd.Timestamp(date)], **row})
    return {code: df}


# ─────────────────────────────────────────────────────────────
# can_trade 4 状态测试
# ─────────────────────────────────────────────────────────────

def test_can_trade_normal_market_passes():
    """正常行情 (无停牌/涨跌停) → (True, '')"""
    broker = ABroker()
    md = _make_market_data()
    can, reason = broker.can_trade("600519", md, "2026-06-09")
    assert can is True
    assert reason == ""


def test_can_trade_no_market_data_blocks():
    """无行情数据 → (False, '无行情数据')"""
    broker = ABroker()
    can, reason = broker.can_trade("600519", {}, "2026-06-09")
    assert can is False
    assert "无行情" in reason


def test_can_trade_suspended_blocks():
    """停牌 → (False, '停牌')"""
    broker = ABroker()
    md = _make_market_data(is_suspend=True)
    can, reason = broker.can_trade("600519", md, "2026-06-09")
    assert can is False
    assert reason == "停牌"


def test_can_trade_limit_up_blocks_buy():
    """涨停 → (False, '涨停无法买入') — 仅阻止 BUY"""
    broker = ABroker()
    md = _make_market_data(is_limit_up=True)
    can, reason = broker.can_trade("600519", md, "2026-06-09")
    assert can is False
    assert reason == "涨停无法买入"


def test_can_trade_limit_down_blocks_sell():
    """跌停 → (False, '跌停无法卖出') — 阻止 SELL (D5 重点)"""
    broker = ABroker()
    md = _make_market_data(is_limit_down=True)
    can, reason = broker.can_trade("600519", md, "2026-06-09")
    assert can is False
    assert reason == "跌停无法卖出"


def test_can_trade_date_filter_uses_target_date():
    """date 参数截取 <= target_date 的最新行 (防止用未来数据)"""
    broker = ABroker()
    # 2 行行情: 06-08 正常 + 06-09 停牌
    df = pd.DataFrame({
        "trade_date": [pd.Timestamp("2026-06-08"), pd.Timestamp("2026-06-09")],
        "close": [10.0, 10.0],
        "is_suspend": [False, True],
    })
    # 查 06-08 → 应不视 06-09 停牌
    can, reason = broker.can_trade("600519", {"600519": df}, "2026-06-08")
    assert can is True, f"查 06-08 应不视 06-09 停牌, 实际 can={can}, reason={reason}"
