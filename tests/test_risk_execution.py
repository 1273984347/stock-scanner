"""风控自动执行 + 阈值修正测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from lib.risk_manager import RiskManager, RiskConfig, ActionExecutor, RiskAction


class TestRiskExecution:
    def setup_method(self):
        self.executed_actions = []
        self.executor = ActionExecutor()
        self.executor.register("STOP_TRADING", lambda a: self.executed_actions.append(a))
        self.executor.register("REDUCE_POSITION", lambda a: self.executed_actions.append(a))

    def test_daily_loss_triggers_reduce(self):
        """日亏损超过3%触发减仓"""
        config = RiskConfig(daily_loss_limit=0.03)
        rm = RiskManager(config=config, executor=self.executor)
        alerts, actions = rm.check_all(
            portfolio_value=100000, positions={},
            daily_return=-0.04, max_drawdown=0.05
        )
        daily_alerts = [a for a in alerts if a.rule_name == "daily_loss_limit"]
        assert len(daily_alerts) == 1
        assert any(a.action_type == "REDUCE_POSITION" for a in actions)

    def test_weekly_loss_triggers_stop(self):
        """周亏损超过8%触发停止交易"""
        config = RiskConfig(weekly_loss_limit=0.08)
        rm = RiskManager(config=config, executor=self.executor)
        for i in range(5):
            alerts, actions = rm.check_all(
                portfolio_value=100000, positions={},
                daily_return=-0.02, max_drawdown=0.10
            )
        weekly_alerts = [a for a in alerts if a.rule_name == "weekly_loss_limit"]
        assert len(weekly_alerts) >= 1
        assert self.executor.is_trading_suspended

    def test_max_drawdown_triggers_stop(self):
        """回撤超过15%触发停止交易"""
        config = RiskConfig(max_drawdown_stop=0.15)
        rm = RiskManager(config=config, executor=self.executor)
        alerts, actions = rm.check_all(
            portfolio_value=100000, positions={},
            daily_return=-0.01, max_drawdown=-0.16
        )
        dd_alerts = [a for a in alerts if a.rule_name == "max_drawdown"]
        assert len(dd_alerts) == 1
        assert any(a.action_type == "STOP_TRADING" for a in actions)

    def test_threshold_values_match_design(self):
        """默认阈值与设计规格一致"""
        config = RiskConfig()
        assert config.daily_loss_limit == 0.03    # 3%
        assert config.max_drawdown_stop == 0.15   # 15%
        assert config.weekly_loss_limit == 0.08   # 8%

    def test_no_action_when_within_limits(self):
        """未超限时不触发动作"""
        config = RiskConfig(daily_loss_limit=0.03, max_drawdown_stop=0.15)
        rm = RiskManager(config=config, executor=self.executor)
        alerts, actions = rm.check_all(
            portfolio_value=100000, positions={},
            daily_return=-0.01, max_drawdown=-0.10
        )
        critical_alerts = [a for a in alerts if a.level == "CRITICAL"]
        assert len(critical_alerts) == 0
        assert len(actions) == 0

    def test_executor_suspend_and_resume(self):
        """交易暂停和恢复"""
        executor = ActionExecutor()
        assert not executor.is_trading_suspended

        stop_action = RiskAction("STOP_TRADING", "ALL", "test", "CRITICAL")
        executor.execute(stop_action)
        assert executor.is_trading_suspended

        resume_action = RiskAction("RESUME_TRADING", "ALL", "resume", "INFO")
        executor.execute(resume_action)
        assert not executor.is_trading_suspended

    def test_executor_unregistered_action(self):
        """未注册的动作不抛异常，返回 False"""
        executor = ActionExecutor()
        action = RiskAction("UNKNOWN_ACTION", "ALL", "test", "WARNING")
        result = executor.execute(action)
        assert result is False

    def test_check_all_returns_tuple(self):
        """check_all 返回 (alerts, actions) 元组"""
        rm = RiskManager()
        result = rm.check_all(portfolio_value=100000, positions={})
        assert isinstance(result, tuple)
        assert len(result) == 2
        alerts, actions = result
        assert isinstance(alerts, list)
        assert isinstance(actions, list)
