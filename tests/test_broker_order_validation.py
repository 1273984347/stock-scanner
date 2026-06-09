"""Order 入参校验测试 (D5.2, 2026-06-09)

LiveBrokerABC.submit_order 在最早期调 validate_order(order)。
非法订单 raise ValueError, 被 submit_order 的 except 兜底返 None (graceful)。

覆盖:
- validate_order 单元: 5 字段 (code/direction/price/shares/date) 各 1 个非法用例 + 1 个全合法
- 端到端: PaperBroker.submit_order 收到非法 Order 返 None 不 crash
- 边界: None / 错类型 / 空字符串 / 0 / 负数 / 错 enum
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import pytest

from lib.broker import validate_order, LiveBrokerABC
from lib.broker_data.datatypes import Order


# ─────────────────────────────────────────────────────────────
# validate_order 单元测试 (5 字段 × 1 非法 + 1 合法 = 6 用例)
# ─────────────────────────────────────────────────────────────

def test_validate_order_empty_code_raises():
    """code 空字符串 → ValueError"""
    with pytest.raises(ValueError, match="order.code"):
        validate_order(Order(code="", direction="BUY", price=10.0,
                             shares=100, date="2026-06-09"))


def test_validate_order_bad_direction_raises():
    """direction 不在 {BUY, SELL} → ValueError"""
    with pytest.raises(ValueError, match="order.direction"):
        validate_order(Order(code="600519", direction="HOLD", price=10.0,
                             shares=100, date="2026-06-09"))


def test_validate_order_negative_price_raises():
    """price <= 0 → ValueError"""
    with pytest.raises(ValueError, match="order.price"):
        validate_order(Order(code="600519", direction="BUY", price=-1.0,
                             shares=100, date="2026-06-09"))


def test_validate_order_zero_shares_raises():
    """shares <= 0 → ValueError"""
    with pytest.raises(ValueError, match="order.shares"):
        validate_order(Order(code="600519", direction="BUY", price=10.0,
                             shares=0, date="2026-06-09"))


def test_validate_order_empty_date_raises():
    """date 空字符串 → ValueError"""
    with pytest.raises(ValueError, match="order.date"):
        validate_order(Order(code="600519", direction="BUY", price=10.0,
                             shares=100, date=""))


def test_validate_order_valid_passes():
    """全合法字段 → 不 raise"""
    # 不应抛任何异常
    validate_order(Order(code="600519", direction="SELL", price=10.0,
                         shares=100, date="2026-06-09"))


# ─────────────────────────────────────────────────────────────
# 类型边界 (1 用例)
# ─────────────────────────────────────────────────────────────

def test_validate_order_type_errors_raises():
    """错类型 (code=int, shares=str) → ValueError"""
    # code 错类型 (int 而非 str) — 实际 dataclass 强制 str, 用绕过构造
    # 用 dict 创建绕过 type check, 然后 validate
    bad_order = Order.__new__(Order)
    object.__setattr__(bad_order, "code", 12345)  # 错类型
    object.__setattr__(bad_order, "direction", "BUY")
    object.__setattr__(bad_order, "price", 10.0)
    object.__setattr__(bad_order, "shares", 100)
    object.__setattr__(bad_order, "date", "2026-06-09")
    object.__setattr__(bad_order, "reason", "")
    with pytest.raises(ValueError, match="order.code"):
        validate_order(bad_order)

    # shares 错类型 (str 而非 int)
    bad_order2 = Order.__new__(Order)
    object.__setattr__(bad_order2, "code", "600519")
    object.__setattr__(bad_order2, "direction", "BUY")
    object.__setattr__(bad_order2, "price", 10.0)
    object.__setattr__(bad_order2, "shares", "100")  # 错类型
    object.__setattr__(bad_order2, "date", "2026-06-09")
    object.__setattr__(bad_order2, "reason", "")
    with pytest.raises(ValueError, match="order.shares"):
        validate_order(bad_order2)


# ─────────────────────────────────────────────────────────────
# 端到端: PaperBroker.submit_order 收到非法 Order 返 None 不 crash
# ─────────────────────────────────────────────────────────────

def test_paper_broker_submit_order_invalid_returns_none():
    """PaperBroker.submit_order 收到非法 Order → 返 None (不 crash)"""
    from lib.broker import PaperBroker

    paper = PaperBroker(initial_cash=100000)
    # 注: PaperBroker._connected 默认 True (initialize)
    # 提交非法订单 (zero shares)
    bad_order = Order(code="600519", direction="BUY", price=10.0,
                       shares=0, date="2026-06-09")
    result = paper.submit_order(bad_order)
    assert result is None, f"非法 Order 应返 None, 实际 {result}"


def test_live_broker_abc_submit_order_invalid_logs_error():
    """LiveBrokerABC.submit_order 收到非法 Order → log error + 返 None"""
    from unittest.mock import patch
    from lib import broker as broker_mod
    from lib.broker import PaperBroker

    paper = PaperBroker(initial_cash=100000)
    # 注: PaperBroker._connected 需为 True 才能走到 validate_order
    bad_order = Order(code="600519", direction="BUY", price=10.0,
                       shares=-1, date="2026-06-09")

    with patch.object(paper, "_connected", True), \
         patch.object(broker_mod, "logger") as mock_logger:
        result = paper.submit_order(bad_order)

    assert result is None
    # 验证 mock_logger.error 被调用且消息含 "订单非法" + "order.shares"
    error_calls = [call.args[0] for call in mock_logger.error.call_args_list]
    assert any("订单非法" in m and "order.shares" in m for m in error_calls), \
        f"应 log 非法订单含 '订单非法' + 'order.shares', 实际: {error_calls}"
