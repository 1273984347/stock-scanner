"""分批减仓止盈单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from config import RiskConfig
from lib.risk.position_manager import PositionManager, Position


@pytest.fixture
def pm_with_partial(tmp_path):
    """PositionManager with partial exit enabled (default config)"""
    cfg = RiskConfig(
        partial_exit_enabled=True,
        partial_exit_levels=[
            {"profit_threshold": 0.10, "sell_ratio": 0.33, "description": "第一档：+10% 减 1/3"},
            {"profit_threshold": 0.20, "sell_ratio": 0.33, "description": "第二档：+20% 减 1/3"},
        ],
        use_fixed_take_profit=False,
    )
    return PositionManager(positions_file=str(tmp_path / "positions.json"), risk_config=cfg)


@pytest.fixture
def pm_with_fixed(tmp_path):
    """PositionManager with fixed take-profit (legacy mode)"""
    cfg = RiskConfig(
        partial_exit_enabled=False,
        use_fixed_take_profit=True,
    )
    return PositionManager(positions_file=str(tmp_path / "positions.json"), risk_config=cfg)


class TestPartialExit:
    def test_first_partial_at_10pct(self, pm_with_partial):
        """First partial exit at +10%"""
        pm = pm_with_partial
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000)
        pos = pm.positions["000001"]

        # At +9%: no trigger
        alerts = pm.check_stop_loss({"000001": 10.90})
        assert len(alerts) == 0
        assert pos.partial_exits_done == 0

        # At +10%: first partial
        alerts = pm.check_stop_loss({"000001": 11.00})
        assert len(alerts) == 1
        assert alerts[0]["action"] == "分批止盈"
        assert alerts[0]["remaining_ratio"] == pytest.approx(0.67)
        assert pos.partial_exits_done == 1
        assert pos.remaining_ratio == pytest.approx(0.67)

    def test_second_partial_at_20pct(self, pm_with_partial):
        """Second partial exit at +20%"""
        pm = pm_with_partial
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000)
        pos = pm.positions["000001"]

        # Trigger first partial
        pm.check_stop_loss({"000001": 11.00})
        assert pos.partial_exits_done == 1

        # At +15%: no second trigger yet
        alerts = pm.check_stop_loss({"000001": 11.50})
        assert len(alerts) == 0
        assert pos.partial_exits_done == 1

        # At +20%: second partial
        alerts = pm.check_stop_loss({"000001": 12.00})
        assert len(alerts) == 1
        assert alerts[0]["action"] == "分批止盈"
        assert alerts[0]["remaining_ratio"] == pytest.approx(0.67 * 0.67)
        assert pos.partial_exits_done == 2
        assert pos.remaining_ratio == pytest.approx(0.67 * 0.67)

    def test_no_more_after_all_levels(self, pm_with_partial):
        """No more exits after all levels executed"""
        pm = pm_with_partial
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000)
        pos = pm.positions["000001"]

        # Trigger both levels
        pm.check_stop_loss({"000001": 11.00})
        pm.check_stop_loss({"000001": 12.00})
        assert pos.partial_exits_done == 2

        # At +30%: no more partial exits
        alerts = pm.check_stop_loss({"000001": 13.00})
        assert len(alerts) == 0
        assert pos.partial_exits_done == 2

    def test_trailing_stop_on_remaining(self, pm_with_partial):
        """Remaining position protected by trailing stop (stop-loss still works)"""
        pm = pm_with_partial
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000,
                          stop_loss=0.05)

        # Trigger first partial
        pm.check_stop_loss({"000001": 11.00})

        # Price drops below stop-loss: still triggers stop
        alerts = pm.check_stop_loss({"000001": 9.40})
        assert len(alerts) == 1
        assert alerts[0]["action"] == "止损"

    def test_partial_exit_disabled(self, pm_with_fixed):
        """When partial exit disabled, no partial exits trigger"""
        pm = pm_with_fixed
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000)

        # At +10%: no partial exit (disabled)
        alerts = pm.check_stop_loss({"000001": 11.00})
        # Should be empty since fixed take-profit is also using default 10%
        # but partial_exit_enabled=False skips partial logic
        # With use_fixed_take_profit=True and pnl >= take_profit, should get fixed TP
        assert len(alerts) == 1
        assert alerts[0]["action"] == "止盈"

    def test_fixed_take_profit_gated_by_config(self, pm_with_fixed):
        """Fixed take-profit only fires when use_fixed_take_profit=True"""
        pm = pm_with_fixed
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000)

        alerts = pm.check_stop_loss({"000001": 11.00})
        assert any(a["action"] == "止盈" for a in alerts)

    def test_fixed_take_profit_disabled_by_default(self, tmp_path):
        """When use_fixed_take_profit=False and partial_exit_enabled=False, no take-profit"""
        cfg = RiskConfig(
            partial_exit_enabled=False,
            use_fixed_take_profit=False,
        )
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"), risk_config=cfg)
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000)

        # At +10%: no alert (both partial and fixed disabled)
        alerts = pm.check_stop_loss({"000001": 11.00})
        assert len(alerts) == 0

    def test_partial_exit_persists_on_save_load(self, tmp_path):
        """Partial exit state survives save/load cycle"""
        cfg = RiskConfig(
            partial_exit_enabled=True,
            partial_exit_levels=[
                {"profit_threshold": 0.10, "sell_ratio": 0.33, "description": "第一档：+10% 减 1/3"},
                {"profit_threshold": 0.20, "sell_ratio": 0.33, "description": "第二档：+20% 减 1/3"},
            ],
        )
        f = str(tmp_path / "positions.json")
        pm = PositionManager(positions_file=f, risk_config=cfg)
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000)

        # Trigger first partial
        pm.check_stop_loss({"000001": 11.00})
        assert pm.positions["000001"].partial_exits_done == 1

        # Reload from file
        pm2 = PositionManager(positions_file=f, risk_config=cfg)
        assert pm2.positions["000001"].partial_exits_done == 1
        assert pm2.positions["000001"].remaining_ratio == pytest.approx(0.67)

    def test_custom_levels(self, tmp_path):
        """Custom partial exit levels and ratios work correctly"""
        cfg = RiskConfig(
            partial_exit_enabled=True,
            partial_exit_levels=[
                {"profit_threshold": 0.05, "sell_ratio": 0.25, "description": "第一档：+5% 减 25%"},
                {"profit_threshold": 0.15, "sell_ratio": 0.25, "description": "第二档：+15% 减 25%"},
                {"profit_threshold": 0.30, "sell_ratio": 0.50, "description": "第三档：+30% 减 50%"},
            ],
            use_fixed_take_profit=False,
        )
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"), risk_config=cfg)
        pm.track_position(code="000001", name="Test", buy_price=10.0, shares=1000)
        pos = pm.positions["000001"]

        # +5%: first partial (25%)
        pm.check_stop_loss({"000001": 10.50})
        assert pos.partial_exits_done == 1
        assert pos.remaining_ratio == pytest.approx(0.75)

        # +15%: second partial (25%)
        pm.check_stop_loss({"000001": 11.50})
        assert pos.partial_exits_done == 2
        assert pos.remaining_ratio == pytest.approx(0.75 * 0.75)

        # +30%: third partial (50%)
        pm.check_stop_loss({"000001": 13.00})
        assert pos.partial_exits_done == 3
        assert pos.remaining_ratio == pytest.approx(0.75 * 0.75 * 0.50)


class TestRegimeAwarePartialExit:
    """V6 市场状态自适应止盈测试"""

    def test_trend_up_raises_thresholds(self):
        """TREND_UP: 止盈目标提高 30%"""
        from lib.risk.partial_exit import PartialExitManager, ExitLevel
        base = [ExitLevel(trigger_pct=0.30, exit_ratio=0.33, description="test")]
        mgr = PartialExitManager(exit_levels=base, regime="TREND_UP")
        assert mgr.exit_levels[0].trigger_pct == pytest.approx(0.30 * 1.3)

    def test_mean_reversion_lowers_thresholds(self):
        """MEAN_REVERSION: 止盈目标降低 30%"""
        from lib.risk.partial_exit import PartialExitManager, ExitLevel
        base = [ExitLevel(trigger_pct=0.30, exit_ratio=0.33, description="test")]
        mgr = PartialExitManager(exit_levels=base, regime="MEAN_REVERSION")
        assert mgr.exit_levels[0].trigger_pct == pytest.approx(0.30 * 0.7)

    def test_high_volatility_early_exit(self):
        """HIGH_VOLATILITY: 止盈目标降低 50%"""
        from lib.risk.partial_exit import PartialExitManager, ExitLevel
        base = [ExitLevel(trigger_pct=0.30, exit_ratio=0.33, description="test")]
        mgr = PartialExitManager(exit_levels=base, regime="HIGH_VOLATILITY")
        assert mgr.exit_levels[0].trigger_pct == pytest.approx(0.30 * 0.5)

    def test_update_regime_changes_levels(self):
        """update_regime 动态更新止盈档位"""
        from lib.risk.partial_exit import PartialExitManager, ExitLevel
        base = [ExitLevel(trigger_pct=0.30, exit_ratio=0.33, description="test")]
        mgr = PartialExitManager(exit_levels=base, regime="MEAN_REVERSION")
        assert mgr.exit_levels[0].trigger_pct == pytest.approx(0.30 * 0.7)

        mgr.update_regime("TREND_UP")
        assert mgr.exit_levels[0].trigger_pct == pytest.approx(0.30 * 1.3)

    def test_default_regime_is_mean_reversion(self):
        """默认市场状态为 MEAN_REVERSION"""
        from lib.risk.partial_exit import PartialExitManager
        mgr = PartialExitManager()
        assert mgr._regime == "MEAN_REVERSION"
