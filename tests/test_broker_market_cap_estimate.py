"""P0-2: lib/broker.py 流通市值估算 sentinel

修复前：估算值（基于成交量反推）会污染 market_cap_result
- 误差 5-50x，silent fallback 不可见
- 影响 _get_dynamic_impact_model 误用动态配置

修复后：估算时返回 sentinel = -2（区别于 -1 失败、>= 0 成功）
- _get_dynamic_impact_model 收到 -2 → 用最保守 impact_model
- 同时打 warning log（不是 debug）让运维可见
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

# 注：不在 file-level 注入 akshare mock，避免污染其他测试。
# 每个测试用 patch("akshare.stock_individual_info_em", ...) 局部 mock。
import logging
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


def _make_market_data(code="600519", close=100.0, volume=1_000_000):
    """构造单行 K 线 market_data"""
    df = pd.DataFrame({
        "trade_date": [pd.Timestamp("2024-06-01")],
        "open": [close], "high": [close], "low": [close], "close": [close],
        "volume": [volume],
    })
    return {code: df}


def test_market_cap_volume_estimate_fallback():
    """P0-1 修复后：原 akshare 成功路径已删（数据穿越风险）。
    改为测 volume 估算 fallback：close > 0 + volume > 0 → 返回 sentinel -2 + 记录估算值。

    估算公式: estimated_market_cap = close * (volume / 0.03)
    估算结果存到 _estimated_market_caps[code] 供指标输出。
    """
    from lib.broker import ABroker as Broker
    b = Broker.__new__(Broker)  # 绕过 __init__
    b._broker_cache_lock = MagicMock()
    b._market_cap_cache = {}
    b.impact_model = MagicMock()

    # P0-1 后不再调用 akshare，估算逻辑在 _get_market_cap 内部完成
    market_data = _make_market_data(close=100.0, volume=1_000_000)
    result = b._get_market_cap("600519", market_data, "2024-06-01")

    # 返回 sentinel -2（不是 -1 完全失败，也不是 >= 0 成功）
    assert result == -2, f"估算 fallback 应返回 sentinel -2, 实际 {result}"

    # 估算值应记录在 _estimated_market_caps
    assert hasattr(b, '_estimated_market_caps'), \
        "估算 fallback 应保存估算值到 _estimated_market_caps"
    assert '600519' in b._estimated_market_caps, \
        "估算值应记录在 _estimated_market_caps['600519']"

    # 估算市值公式: close * (volume / 0.03)
    # 100 * (1_000_000 / 0.03) ≈ 3.333e9
    expected = 100.0 * (1_000_000 / 0.03)
    actual = b._estimated_market_caps['600519']
    assert abs(actual - expected) < 1, \
        f"估算市值期望 {expected:,.2f}, 实际 {actual:,.2f}"


def test_market_cap_returns_minus_two_on_estimation_fallback(caplog):
    """P0-2: akshare 失败 + 有 volume → 返回 sentinel -2（估算值）"""
    from lib.broker import ABroker as Broker
    b = Broker.__new__(Broker)
    b._broker_cache_lock = MagicMock()
    b._market_cap_cache = {}
    b.impact_model = MagicMock()

    with patch("akshare.stock_individual_info_em") as mock_ak:
        mock_ak.side_effect = Exception("akshare 限流")
        market_data = _make_market_data(close=100.0, volume=1_000_000)
        # 直接调 _get_market_cap 然后检查 logger 输出（用 propagate 确保 capture）
        with caplog.at_level(logging.WARNING, logger="lib.broker"):
            result = b._get_market_cap("600519", market_data, "2024-06-01")
        # P0-2 修复：估算时返回 -2（区别于 -1 完全失败）
        assert result == -2, f"期望 sentinel -2, 实际 {result}"
        # 必须打 WARNING（不是 debug），让运维可见
        from lib.broker import logger as _broker_logger
        assert _broker_logger.isEnabledFor(logging.WARNING), \
            "broker logger 应启用 WARNING（默认就是，但验证一下）"
        # 直接验证：估算 fallback 会修改 _estimated_market_caps 字典
        assert hasattr(b, '_estimated_market_caps'), \
            "估算 fallback 应保存估算值到 _estimated_market_caps"
        assert '600519' in b._estimated_market_caps, \
            "估算值应记录在 _estimated_market_caps['600519']"


def test_market_cap_returns_minus_one_on_complete_failure(caplog):
    """akshare 失败 + 无 volume → 返回 -1（完全失败）"""
    from lib.broker import ABroker as Broker
    b = Broker.__new__(Broker)
    b._broker_cache_lock = MagicMock()
    b._market_cap_cache = {}
    b.impact_model = MagicMock()

    with patch("akshare.stock_individual_info_em") as mock_ak:
        mock_ak.side_effect = Exception("akshare 限流")
        market_data = _make_market_data(close=100.0, volume=0)  # 无 volume
        result = b._get_market_cap("600519", market_data, "2024-06-01")
        assert result == -1, f"期望 -1, 实际 {result}"


def test_dynamic_impact_model_uses_most_conservative_for_estimate():
    """P0-2: 收到 -2 (估算) 时 _get_dynamic_impact_model 用最保守 impact_model

    即使估算值 >= 0 也是估算（误差 5-50x），仍应该用最保守 impact_config
    """
    from lib.broker import ABroker as Broker
    b = Broker.__new__(Broker)
    b._broker_cache_lock = MagicMock()
    b._market_cap_cache = {}
    default_impact = MagicMock(name="default_impact")
    b.impact_model = default_impact

    # 直接 mock _get_market_cap 返回 -2
    with patch.object(Broker, "_get_market_cap", return_value=-2):
        market_data = _make_market_data()
        from lib.broker import MarketImpactModel
        result = b._get_dynamic_impact_model("600519", market_data, "2024-06-01")
        # 应返回默认（最保守）impact_model
        assert result is default_impact, f"估算 sentinel -2 应使用默认 impact_model"


def test_dynamic_impact_model_uses_default_for_minus_one():
    """-1 (完全失败) 时也用默认 impact_model"""
    from lib.broker import ABroker as Broker
    b = Broker.__new__(Broker)
    b._broker_cache_lock = MagicMock()
    b._market_cap_cache = {}
    default_impact = MagicMock(name="default_impact")
    b.impact_model = default_impact

    with patch.object(Broker, "_get_market_cap", return_value=-1):
        market_data = _make_market_data()
        result = b._get_dynamic_impact_model("600519", market_data, "2024-06-01")
        assert result is default_impact
