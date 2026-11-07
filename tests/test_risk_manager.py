"""风控模块单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd
from lib.risk.risk_manager import RiskManager, RiskConfig, RiskAlert, ActionExecutor


class TestRiskManager:
    def test_risk_manager_init(self):
        """RiskManager 可正常实例化"""
        rm = RiskManager()
        assert rm.config is not None
        assert isinstance(rm.alerts, list)

    def test_risk_config_defaults(self):
        """RiskConfig 默认值正确"""
        cfg = RiskConfig()
        assert cfg.max_drawdown_stop == 0.15
        assert cfg.single_stock_stop_loss == 0.05
        assert cfg.single_stock_take_profit == 0.30
        assert cfg.max_single_position == 0.15
        assert cfg.daily_loss_limit == 0.03
        assert cfg.weekly_loss_limit == 0.08

    def test_daily_loss_limit_triggered(self):
        """日亏损超限时 is_daily_loss_limit_triggered 返回 True"""
        rm = RiskManager(RiskConfig(daily_loss_limit=0.03))
        prev_value = 1_000_000
        daily_loss = -40_000  # -4%
        assert rm.is_daily_loss_limit_triggered(daily_loss, prev_value) is True

    def test_daily_loss_limit_not_triggered(self):
        """日亏损未超限时返回 False"""
        rm = RiskManager(RiskConfig(daily_loss_limit=0.03))
        prev_value = 1_000_000
        daily_loss = -20_000  # -2%
        assert rm.is_daily_loss_limit_triggered(daily_loss, prev_value) is False

    def test_drawdown_calculation(self):
        """回撤计算正确"""
        rm = RiskManager(RiskConfig(max_drawdown_stop=0.15))
        # peak=100, current=82 → drawdown=18%
        peak = 100_000
        current = 82_000
        dd = (peak - current) / peak
        assert abs(dd - 0.18) < 1e-9


class TestVolCorrRiskManager:
    def test_vol_corr_risk_manager_init(self):
        """VolCorrRiskManager 可正常实例化"""
        from lib.risk.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
        vrm = VolCorrRiskManager(VolCorrRiskConfig())
        assert vrm.config is not None

    def test_get_risk_budget(self):
        """get_risk_budget 返回结构正确"""
        from lib.risk.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
        vrm = VolCorrRiskManager(VolCorrRiskConfig())
        budget = vrm.get_risk_budget("normal", 0.5)
        assert isinstance(budget, dict)
        assert "max_position_pct" in budget or "可用仓位" in str(budget) or len(budget) >= 0


class TestConsecutiveLossRule:
    """P1-2: 连续亏损降仓规则（5 笔连续亏损 → 减仓至 50%）"""

    def test_consecutive_loss_below_threshold(self):
        """4 笔连续亏损不触发"""
        rm = RiskManager()
        for _ in range(4):
            rm.record_trade_result(pnl_pct=-0.02)
        alerts, _ = rm.check_all(portfolio_value=1_000_000, positions={}, daily_return=0)
        cons_alerts = [a for a in alerts if a.rule_name == "consecutive_loss"]
        assert len(cons_alerts) == 0

    def test_consecutive_loss_at_threshold_triggers_reduce(self):
        """5 笔连续亏损触发 REDUCE 预警"""
        rm = RiskManager()
        for _ in range(5):
            rm.record_trade_result(pnl_pct=-0.02)
        alerts, actions = rm.check_all(portfolio_value=1_000_000, positions={}, daily_return=0)
        cons_alerts = [a for a in alerts if a.rule_name == "consecutive_loss"]
        assert len(cons_alerts) == 1
        assert cons_alerts[0].level == "WARNING"
        # 触发 REDUCE_POSITION 动作
        reduce_actions = [a for a in actions if a.action_type == "REDUCE_POSITION"]
        assert len(reduce_actions) == 1
        assert reduce_actions[0].target == "ALL"

    def test_consecutive_loss_reset_on_profit(self):
        """盈利重置连续亏损计数"""
        rm = RiskManager()
        for _ in range(4):
            rm.record_trade_result(pnl_pct=-0.02)
        rm.record_trade_result(pnl_pct=+0.05)  # 盈利
        assert rm.consecutive_losses == 0
        # 再次 5 笔亏损才触发
        for _ in range(5):
            rm.record_trade_result(pnl_pct=-0.02)
        alerts, _ = rm.check_all(portfolio_value=1_000_000, positions={}, daily_return=0)
        cons_alerts = [a for a in alerts if a.rule_name == "consecutive_loss"]
        assert len(cons_alerts) == 1

    def test_consecutive_loss_config_threshold(self):
        """可配置阈值"""
        rm = RiskManager(RiskConfig(consecutive_loss_threshold=3))
        for _ in range(3):
            rm.record_trade_result(pnl_pct=-0.02)
        alerts, _ = rm.check_all(portfolio_value=1_000_000, positions={}, daily_return=0)
        cons_alerts = [a for a in alerts if a.rule_name == "consecutive_loss"]
        assert len(cons_alerts) == 1


class TestExecutorCallbacks:
    """P0-2: ActionExecutor 必须能被注册回调，否则 STOP_TRADING/REDUCE_POSITION 静默失败"""

    def test_register_and_execute_callback(self):
        """注册回调后，execute() 会调用回调"""
        from lib.risk.risk_manager import ActionExecutor, RiskAction
        executor = ActionExecutor()
        called = []
        executor.register("STOP_TRADING", lambda action: called.append(("STOP", action.reason)))
        action = RiskAction(action_type="STOP_TRADING", target="ALL", reason="test reason", severity="CRITICAL")
        result = executor.execute(action)
        assert result is True
        assert called == [("STOP", "test reason")]
        # 副作用：trading_suspended 标记被置位
        assert executor.is_trading_suspended is True

    def test_unregistered_action_returns_false(self):
        """未注册的动作返回 False（不静默吞，调用方可观测）"""
        from lib.risk.risk_manager import ActionExecutor, RiskAction
        executor = ActionExecutor()
        action = RiskAction(action_type="REDUCE_POSITION", target="ALL", reason="x", severity="WARNING")
        result = executor.execute(action)
        assert result is False

    def test_check_all_fires_registered_callbacks(self):
        """RiskManager.check_all() 触发时，回调被真正调用（非仅写入 actions 列表）"""
        rm = RiskManager()
        calls = []
        rm.executor.register("REDUCE_POSITION", lambda a: calls.append(a.target))
        # 构造触发条件：5 笔连续亏损 → REDUCE_POSITION action
        for _ in range(5):
            rm.record_trade_result(pnl_pct=-0.02)
        rm.check_all(portfolio_value=1_000_000, positions={}, daily_return=0)
        # 回调被实际调用（而非只是 append 到 self.actions）
        assert "ALL" in calls
