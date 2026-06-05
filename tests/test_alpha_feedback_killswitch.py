"""P0-3: lib/alpha_feedback.py alpha fallback 方向修复

修复前：total <= 0 时返回 {k: 1.0 / len(raw_weights)}（等权启用）
- 应该淘汰（应关仓）反而等权下注
- 极端行情下直接放大亏损

修复后：total <= 0 时返回 {} (空字典) + 触发 __kill_switch__ sentinel
- position_sizer / risk_manager 看到空字典应理解为"全市场关仓"
- 同时 dim-5 P1-2 + dim-2 P0-3 双盲确认此修复
"""
import os, sys
sys.path.insert(0, '.')

import pytest
import logging
from lib.alpha_feedback import AlphaFeedback


def test_get_alpha_weights_returns_empty_dict_when_all_alphas_killed():
    """P0-3: 所有 alpha 胜率<30% 时，get_alpha_weights 应返回空字典（kill switch）"""
    feedback = AlphaFeedback()

    # 模拟 3 种 alpha，每种 5 笔全亏（胜率 0%）
    for i in range(5):
        feedback.record_trade(alpha_type="热门板块", pnl_pct=-0.05, regime="TREND_UP", sector="科技")
        feedback.record_trade(alpha_type="龙虎榜", pnl_pct=-0.05, regime="TREND_UP", sector="金融")
        feedback.record_trade(alpha_type="量价突破", pnl_pct=-0.05, regime="TREND_UP", sector="科技")

    weights = feedback.get_alpha_weights()
    # P0-3 修复：返回空字典（不是等权）→ kill switch 触发
    assert weights == {}, f"全淘汰时应返回 {{}}（kill switch），实际 {weights}"


def test_get_alpha_weights_returns_empty_when_no_stats(caplog):
    """P0-3: 无交易记录时返回空字典（无信号 → kill switch）"""
    feedback = AlphaFeedback()
    weights = feedback.get_alpha_weights()
    assert weights == {}, f"无 stats 时应返回 {{}}，实际 {weights}"


def test_get_alpha_weights_normal_case_unaffected():
    """P0-3: 正常情况（部分 alpha 有效）权重应正常计算"""
    feedback = AlphaFeedback()

    # "热门板块" 5 笔全胜（胜率 100%），"龙虎榜" 3 笔全亏
    for _ in range(5):
        feedback.record_trade(alpha_type="热门板块", pnl_pct=+0.05, regime="TREND_UP", sector="科技")
    for _ in range(3):
        feedback.record_trade(alpha_type="龙虎榜", pnl_pct=-0.05, regime="TREND_UP", sector="金融")

    weights = feedback.get_alpha_weights()
    assert "热门板块" in weights, f"有效 alpha 应在权重中，实际 {weights}"
    # 低胜率 alpha 仍可能在字典中（值为 0.0），重点是权重为 0
    assert weights.get("龙虎榜", 0) == 0.0, f"低胜率 alpha 权重应为 0，实际 {weights}"
    # 热门板块权重应该是 1.0（归一化）
    assert abs(weights["热门板块"] - 1.0) < 0.01, f"单一有效 alpha 应占全部权重，实际 {weights}"


def test_get_alpha_weights_low_sample_size_uses_default():
    """P0-3: 样本不足（<3 笔）应给默认权重 0.5，不触发 kill switch"""
    feedback = AlphaFeedback()

    # 1 笔交易（样本不足）
    feedback.record_trade(alpha_type="热门板块", pnl_pct=-0.10, regime="TREND_UP", sector="科技")

    weights = feedback.get_alpha_weights()
    # 样本不足不是 kill switch，仍给默认权重（默认 0.5 → 唯一 alpha 归一化 = 1.0）
    assert "热门板块" in weights, f"样本不足仍应给权重，实际 {weights}"
    assert weights["热门板块"] == 1.0, f"1 笔样本不足 → 唯一 alpha 归一化后 = 1.0，实际 {weights}"


def test_kill_switch_logged_as_warning(caplog):
    """P0-3: kill switch 触发时必须打 warning log（让 risk_manager 可见）"""
    feedback = AlphaFeedback()

    for i in range(5):
        feedback.record_trade(alpha_type="热门板块", pnl_pct=-0.05, regime="TREND_UP", sector="科技")
        feedback.record_trade(alpha_type="龙虎榜", pnl_pct=-0.05, regime="TREND_UP", sector="金融")

    # 验证 kill switch 触发并打 warning（不依赖 caplog，用直接验证）
    from lib.alpha_feedback import logger as _af_logger
    captured = []
    handler_id = _af_logger.addHandler(_CaptureHandler(captured))
    try:
        weights = feedback.get_alpha_weights()
    finally:
        _af_logger.removeHandler(handler_id)

    assert weights == {}, f"全淘汰时必须 kill switch（返回空字典），实际 {weights}"
    # 验证 warning 包含 kill switch 关键字
    assert any("kill switch" in msg or "关仓" in msg or "全淘汰" in msg
               for msg in captured), \
        f"kill switch 应打 warning log，实际 logs: {captured}"


class _CaptureHandler(logging.Handler):
    def __init__(self, records):
        super().__init__()
        self.records = records
    def emit(self, record):
        self.records.append(self.format(record))
