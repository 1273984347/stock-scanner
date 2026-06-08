"""Position constraint tests — G6: prohibit loss-adding"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from lib.position_manager import PositionManager, Position
from trading_journal import TradeRecord, TradeAttribution, analyze_trade


class TestPositionConstraints:
    def test_can_add_to_profitful(self, tmp_path):
        """Profitable position allows adding"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        pm.track_position(code="000001", name="平安银行", buy_price=10.00,
                          shares=1000)
        allowed, reason = pm.can_add_position("000001", current_price=12.00)
        assert allowed is True
        assert "允许加仓" in reason

    def test_cannot_add_to_losing(self, tmp_path):
        """Losing position blocks adding"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        pm.track_position(code="000001", name="平安银行", buy_price=10.00,
                          shares=1000)
        allowed, reason = pm.can_add_position("000001", current_price=9.00)
        assert allowed is False
        assert "禁止亏损补仓" in reason

    def test_max_add_limit(self, tmp_path):
        """加仓 always allowed if profitable (剩余比例上限 已废弃)

        P0-6 修复 (2026-06-08): 旧 `pos.remaining_ratio >= 1.5` guard 是死代码
        - remaining_ratio 在 _check_partial_exit (line 319) 只 *= 减小
        - 从未在 add_position 路径 += 增大
        - 旧 guard 永远不触发，删除后加仓逻辑正常生效
        - 真正的 max add limit 应基于"初始仓位 × max_multiplier"跟踪（见 TODO）
        """
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        pm.track_position(code="000001", name="平安银行", buy_price=10.00,
                          shares=1000)
        pos = pm.positions["000001"]
        pos.remaining_ratio = 1.5  # 旧测试遗留 - 不再影响加仓决策
        allowed, reason = pm.can_add_position("000001", current_price=12.00)
        # 新行为：加仓 always allowed (盈利时)
        assert allowed is True
        # TODO: 实现真正 max add limit (基于 add_count * initial_size)
        # 见 position_manager.can_add_position P0-6 修复注释

    def test_new_position_always_allowed(self, tmp_path):
        """New position (not in portfolio) always allowed"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        allowed, reason = pm.can_add_position("999999", current_price=10.00)
        assert allowed is True
        assert "新建仓位" in reason

    def test_losing_at_breakeven_blocks(self, tmp_path):
        """Position at exactly breakeven (pnl=0) blocks adding"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        pm.track_position(code="000001", name="平安银行", buy_price=10.00,
                          shares=1000)
        allowed, reason = pm.can_add_position("000001", current_price=10.00)
        assert allowed is False
        assert "禁止亏损补仓" in reason


class TestTradeAttribution:
    def test_attribution_structure(self):
        """analyze_trade returns TradeAttribution with correct fields"""
        record = {
            "market_regime": "TREND_UP",
            "pnl_pct": 10.0,
            "signal_type": "MA5_MA21_GC",
            "max_runup_pct": 15.0,
            "max_drawdown_pct": -2.0,
            "entry_date": "2024-01-01",
            "exit_date": "2024-01-20",
        }
        attr = analyze_trade(record)
        assert isinstance(attr, TradeAttribution)
        assert hasattr(attr, "market_contribution")
        assert hasattr(attr, "sector_contribution")
        assert hasattr(attr, "signal_quality")
        assert hasattr(attr, "timing_score")
        assert hasattr(attr, "main_factor")

    def test_trending_up_positive_market(self):
        """TREND_UP regime gives positive market contribution for profitable trade"""
        record = {
            "market_regime": "TREND_UP",
            "pnl_pct": 10.0,
            "signal_type": "MA5_MA21_GC",
            "max_runup_pct": 15.0,
        }
        attr = analyze_trade(record)
        assert attr.market_contribution > 0

    def test_trending_down_negative_market(self):
        """TREND_DOWN regime gives negative market contribution"""
        record = {
            "market_regime": "TREND_DOWN",
            "pnl_pct": 10.0,
            "signal_type": "MA5_MA21_GC",
            "max_runup_pct": 15.0,
        }
        attr = analyze_trade(record)
        assert attr.market_contribution < 0

    def test_timing_score_captures_profit(self):
        """Timing score reflects how much of available profit was captured"""
        record = {
            "market_regime": "TREND_UP",
            "pnl_pct": 10.0,
            "signal_type": "MA5_MA21_GC",
            "max_runup_pct": 20.0,  # captured half of max runup
        }
        attr = analyze_trade(record)
        assert 0.4 < attr.timing_score < 0.6

    def test_main_factor_is_string(self):
        """Main factor is always a non-empty string"""
        record = {
            "market_regime": "UNKNOWN",
            "pnl_pct": -5.0,
            "signal_type": "KDJ_CROSS",
            "max_runup_pct": 3.0,
        }
        attr = analyze_trade(record)
        assert isinstance(attr.main_factor, str)
        assert len(attr.main_factor) > 0

    def test_emotion_fields_on_trade_entry(self):
        """TradeEntry supports emotion_state, rule_violated, violation_desc"""
        from trading_journal import TradeEntry
        entry = TradeEntry(
            code="000001",
            name="平安银行",
            market_regime="TREND_UP",
            entry_price=10.0,
            entry_date="2024-01-01",
            signal_type="MA5_MA21_GC",
            atr_at_entry=1.5,
            stop_loss=9.0,
            position_pct=10.0,
            emotion_state="焦虑",
            rule_violated=True,
            violation_desc="追高买入",
        )
        assert entry.emotion_state == "焦虑"
        assert entry.rule_violated is True
        assert entry.violation_desc == "追高买入"
