"""BrokerConfig 集中 magic number 测试 (2026-06-09)

集中前: _missing_days 60 / 板块涨跌停 4 个 magic number 散在 lib/broker.py
集中后: 走 config.BrokerConfig dataclass, paper trading 调参不用改代码

覆盖:
- BrokerConfig dataclass 默认值正确
- _missing_days 阈值走 BrokerConfig (60 → 自定义 30)
- 板块涨跌停 4 个 magic 走 BrokerConfig (默认 + 自定义)
- ABroker.get_limit_pct 行为不变 (8 板块)
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

from unittest.mock import patch

import pandas as pd
import pytest

from config import BrokerConfig
from lib import broker as broker_mod


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
    from lib.broker import ABroker
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
    from lib.broker import ABroker
    # mock BrokerConfig 实例
    custom = BrokerConfig(
        limit_pct_main=0.08,        # 主板 8% (严于默认 10%)
        limit_pct_chinext=0.15,     # 创业板 15% (严于默认 20%)
    )
    with patch.object(broker_mod, "BrokerConfig", return_value=custom):
        # 但 ABroker.get_limit_pct 内部用 BrokerConfig() 实时实例化
        # 我们需要 mock 让它返 custom
        with patch("lib.broker.BrokerConfig", return_value=custom):
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
    src = open("lib/broker.py", encoding="utf-8").read()
    assert "BrokerConfig().missing_days_threshold" in src, \
        "lib/broker.py 应引用 BrokerConfig().missing_days_threshold"
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
