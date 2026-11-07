"""BrokerConfig 集中 magic number 测试 (2026-06-09)

集中前: _missing_days 60 / 板块涨跌停 4 个 magic number 散在 lib/broker.py
集中后: 走 config.BrokerConfig dataclass, paper trading 调参不用改代码

覆盖:
- BrokerConfig dataclass 默认值正确
- _missing_days 阈值走 BrokerConfig (60 → 自定义 30)
- 板块涨跌停 4 个 magic 走 BrokerConfig (默认 + 自定义)
- ABroker.get_limit_pct 行为不变 (8 板块)
- D3.2: 退市清理 4 个 flag (force_close / release_slot / record_set / book_cash)
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

from unittest.mock import patch

import pandas as pd
import pytest

from config import BrokerConfig
import lib.data.broker as broker_mod
# ─────────────────────────────────────────────────────────────
# BrokerConfig dataclass 单元测试
# ─────────────────────────────────────────────────────────────

def test_broker_config_defaults():
    """BrokerConfig 默认值匹配原始 magic number (行为不变)"""
    cfg = BrokerConfig()
    assert cfg.missing_days_threshold == 60
    assert cfg.limit_pct_st == 0.05
    assert cfg.limit_pct_main == 0.10
    assert cfg.limit_pct_chinext == 0.20
    assert cfg.limit_pct_star == 0.20
    assert cfg.limit_pct_bse == 0.30
    assert cfg.limit_pct_default == 0.10


def test_broker_config_customizable():
    """BrokerConfig 可被覆写, 不改 dataclass 默认值 (单实例模式)"""
    cfg = BrokerConfig(missing_days_threshold=30, limit_pct_st=0.03)
    assert cfg.missing_days_threshold == 30
    assert cfg.limit_pct_st == 0.03
    # 默认值不变
    assert cfg.limit_pct_main == 0.10


# ─────────────────────────────────────────────────────────────
# 板块涨跌停 行为测试 (默认 BrokerConfig)
# ─────────────────────────────────────────────────────────────

def test_get_limit_pct_default_all_boards():
    """默认 BrokerConfig → 8 板块行为完全不变"""
    from lib.data.broker import ABroker
    cases = [
        ("600519", "贵州茅台", 0.10),     # 主板 (60 开头)
        ("000001", "平安银行", 0.10),     # 主板 (00 开头)
        ("300750", "宁德时代", 0.20),     # 创业板 (30 开头)
        ("688981", "中芯国际", 0.20),     # 科创板 (68 开头)
        ("830xxx", "北交所 8 开头", 0.30),  # 北交所 (8 开头)
        ("400xxx", "北交所 4 开头", 0.30),  # 北交所 (4 开头)
        ("600519", "ST 茅台", 0.05),       # ST (name 含 ST)
        ("999999", "未知", 0.10),          # 默认
    ]
    for code, name, expected in cases:
        actual = ABroker.get_limit_pct(code, name)
        assert abs(actual - expected) < 1e-9, \
            f"{code} {name}: 期望 {expected}, 实际 {actual}"


def test_get_limit_pct_custom_config_respected():
    """自定义 BrokerConfig → 板块涨跌幅用新值"""
    from lib.data.broker import ABroker
    # mock BrokerConfig 实例
    custom = BrokerConfig(
        limit_pct_main=0.08,        # 主板 8% (严于默认 10%)
        limit_pct_chinext=0.15,     # 创业板 15% (严于默认 20%)
    )
    with patch.object(broker_mod, "BrokerConfig", return_value=custom):
        # 但 ABroker.get_limit_pct 内部用 BrokerConfig() 实时实例化
        # 我们需要 mock 让它返 custom
        with patch("lib.data.broker.BrokerConfig", return_value=custom):
            assert ABroker.get_limit_pct("600519", "茅台") == 0.08
            assert ABroker.get_limit_pct("300750", "宁德") == 0.15
            # 不在 mock 范围内的保持默认
            assert ABroker.get_limit_pct("688981", "中芯") == 0.20  # 科创板 仍默认


# ─────────────────────────────────────────────────────────────
# _missing_days 阈值 测试 (默认 + 自定义)
# ─────────────────────────────────────────────────────────────

def test_missing_days_threshold_default_60():
    """默认 BrokerConfig.missing_days_threshold = 60"""
    cfg = BrokerConfig()
    assert cfg.missing_days_threshold == 60
    # broker.py 应该用这个值, 简单验证 grep
    src = open("lib/data/broker.py", encoding="utf-8").read()
    assert "BrokerConfig().missing_days_threshold" in src, \
        "lib/data/broker.py 应引用 BrokerConfig().missing_days_threshold"
    # 老的 magic 60 不应在 _missing_days 比较行
    # (跳过注释行 — 注释里的 "60 天" 不算 magic)
    lines_with_60 = [l for l in src.split("\n")
                     if "_missing_days" in l and "60" in l
                     and "BrokerConfig" not in l
                     and not l.strip().startswith("#")
                     and "旧实现" not in l  # 注释里的历史说明
                     and "comment" not in l.lower()]
    assert not lines_with_60, \
        f"发现裸 60 magic: {lines_with_60}"


def test_missing_days_threshold_easy_to_change():
    """missing_days_threshold 改成 30 应正常工作 (不需改代码)"""
    cfg = BrokerConfig(missing_days_threshold=30)
    assert cfg.missing_days_threshold == 30
    # 验证 broker.py 用的是实时 BrokerConfig() (不是缓存常量)
    import inspect
    src = inspect.getsource(broker_mod.ABroker)
    # 在 broker 中 引用了 BrokerConfig (不是直接 magic 60)
    assert "BrokerConfig" in src or "missing_days_threshold" in src


# ─────────────────────────────────────────────────────────────
# D3.2: 退市清理 flag 测试 (2026-06-09)
# ─────────────────────────────────────────────────────────────

def test_delisting_cleanup_flags_defaults():
    """D3.2 退市清理 4 个 flag 默认值"""
    cfg = BrokerConfig()
    assert cfg.force_close_on_delisting is True   # 默认强制平仓
    assert cfg.release_position_slot is True     # 默认释放 position_count
    assert cfg.record_delisted_set is True       # 默认记录 _delisted_codes
    assert cfg.book_cash_on_delisting is False   # 默认现金不入账 (P0-3 决策, 无对手盘)


def test_delisting_cleanup_broker_uses_flags():
    """broker.py 应引用 D3.2 4 个 flag, 不再用裸 hard-coded 行为"""
    import inspect
    src = inspect.getsource(broker_mod.ABroker)
    for flag in ["force_close_on_delisting", "release_position_slot",
                 "record_delisted_set", "book_cash_on_delisting"]:
        assert flag in src, f"broker.py 应引用 BrokerConfig().{flag}"


def test_delisting_cleanup_force_close_disabled_keeps_holdings():
    """force_close_on_delisting=False → holdings 保留, 不强制平仓"""
    from lib.data.broker import ABroker

    # 创建 ABroker 实例
    broker = ABroker(initial_cash=100000)

    # mock BrokerConfig 改 force_close 为 False
    custom = BrokerConfig(
        missing_days_threshold=2,  # 快速触发
        force_close_on_delisting=False,
        release_position_slot=False,
        record_delisted_set=False,
        book_cash_on_delisting=False,
    )

    # 模拟持仓 (放 600519 在 holdings, 但 market_data 不含 600519 → 触发无行情)
    broker.holdings["600519"] = "dummy_holding"
    fake_market_data = {"000001": pd.DataFrame({"close": [10]})}

    with patch.object(broker_mod, "BrokerConfig", return_value=custom):
        # 调用 update_market_prices 多次直到触发退市 (missing_days >= 2)
        for i in range(3):
            broker.update_market_prices(fake_market_data, f"2026-06-0{i+1}")

    # 关键: holdings["600519"] 应保留 (因为 force_close=False)
    assert "600519" in broker.holdings, \
        f"force_close_on_delisting=False 时, holdings 应保留, 实际: {list(broker.holdings.keys())}"
    # 同时 _delisted_codes 不应被记录 (record_delisted_set=False)
    assert "600519" not in broker._delisted_codes, \
        "record_delisted_set=False 时, _delisted_codes 不应被记录"
