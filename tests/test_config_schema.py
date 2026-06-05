"""P0-1: config.py JSON 热更新 schema/type/range 验证

修复前：_apply_overrides 只查字段存在性，不查类型/范围
- stop_loss_pct: "5.0"（字符串）会被直接 setattr
- 风控 if 语句 TypeError → 风控 silent 失效
- 破坏所有 dim-4 修复

修复后：写 CONFIG_SCHEMA 字典 + _apply_overrides 加 type/range 验证
- 类型错 → warning + 跳过（保持原值）
- 范围外 → warning + 跳过
- 未知字段 → warning + 跳过（已有）
"""
import os, sys
sys.path.insert(0, '.')

import pytest
from dataclasses import dataclass, field
from config import (
    SystemConfig, RiskConfig, ScanConfig, FactorConfig, MLConfig,
    _apply_overrides, _CONFIG_SCHEMA,
)


def test_schema_dict_exists_and_covers_critical_fields():
    """P0-1: CONFIG_SCHEMA 字典必须存在并覆盖关键风控字段"""
    assert isinstance(_CONFIG_SCHEMA, dict), "CONFIG_SCHEMA 必须是字典"
    # 关键风控字段必须被 schema 覆盖（key 用类名匹配）
    assert "RiskConfig" in _CONFIG_SCHEMA, "schema 必须包含 RiskConfig 类"
    risk_schema = _CONFIG_SCHEMA["RiskConfig"]
    assert "stop_loss_pct" in risk_schema, "stop_loss_pct 必须在 schema 中"
    assert "take_profit_pct" in risk_schema, "take_profit_pct 必须在 schema 中"
    assert "max_single_weight" in risk_schema, "max_single_weight 必须在 schema 中"


def test_apply_overrides_rejects_string_for_float_field():
    """P0-1: stop_loss_pct 是字符串 "5.0" 时，_apply_overrides 必须拒绝（保持原值）"""
    cfg = SystemConfig()
    original_stop_loss = cfg.risk.stop_loss_pct  # 默认 5.0

    # 用户错误地填了字符串
    bad_overrides = {"risk": {"stop_loss_pct": "5.0"}}
    _apply_overrides(cfg, bad_overrides)

    # 修复后期望：stop_loss_pct 保持原值 5.0（不是字符串"5.0"）
    assert cfg.risk.stop_loss_pct == original_stop_loss, \
        f"字符串值必须被拒绝，应保持原值 {original_stop_loss}，实际 {cfg.risk.stop_loss_pct}"


def test_apply_overrides_rejects_value_out_of_range():
    """P0-1: 范围外的值（如 stop_loss_pct: 50.0）必须被拒绝"""
    cfg = SystemConfig()
    original_stop_loss = cfg.risk.stop_loss_pct

    # stop_loss_pct 范围 [0.5, 20.0]（合理范围），50.0 超范围
    bad_overrides = {"risk": {"stop_loss_pct": 50.0}}
    _apply_overrides(cfg, bad_overrides)

    assert cfg.risk.stop_loss_pct == original_stop_loss, \
        f"超范围值必须被拒绝，应保持原值 {original_stop_loss}，实际 {cfg.risk.stop_loss_pct}"


def test_apply_overrides_accepts_valid_value():
    """P0-1: 合法值应正常应用"""
    cfg = SystemConfig()

    valid_overrides = {"risk": {"stop_loss_pct": 7.5}}  # 在 [0.5, 20] 范围内
    _apply_overrides(cfg, valid_overrides)

    assert cfg.risk.stop_loss_pct == 7.5, \
        f"合法值应被应用，实际 {cfg.risk.stop_loss_pct}"


def test_apply_overrides_rejects_unknown_field():
    """P0-1: 未知字段被拒绝（保持原行为）"""
    cfg = SystemConfig()

    unknown_overrides = {"risk": {"nonexistent_field": 99.9}}
    _apply_overrides(cfg, unknown_overrides)  # 不应抛异常

    # 未知字段不影响其他合法字段
    assert cfg.risk.stop_loss_pct == 5.0  # 默认值


def test_apply_overrides_rejects_bool_for_int_field():
    """P0-1: 类型错（bool 给 int）应被拒绝"""
    cfg = SystemConfig()
    original_max_hold = cfg.risk.max_hold_days  # 默认 5 (int)

    bad_overrides = {"risk": {"max_hold_days": True}}  # bool 不该给 int
    _apply_overrides(cfg, bad_overrides)

    assert cfg.risk.max_hold_days == original_max_hold, \
        f"bool 错给 int 应被拒绝，实际 {cfg.risk.max_hold_days}"
