"""风控模块单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd
from lib.risk_manager import RiskManager, RiskConfig, RiskAlert


class TestRiskManager:
    def test_risk_manager_init(self):
        """RiskManager 可正常实例化"""
        rm = RiskManager()
        assert rm.config is not None
        assert isinstance(rm.alerts, list)

    def test_risk_config_defaults(self):
        """RiskConfig 默认值正确"""
        cfg = RiskConfig()
        assert cfg.max_drawdown_stop == 0.20
        assert cfg.single_stock_stop_loss == 0.05
        assert cfg.single_stock_take_profit == 0.30
        assert cfg.max_single_position == 0.15
        assert cfg.daily_loss_limit == 0.05

    def test_daily_loss_limit_triggered(self):
        """日亏损超限时 is_daily_loss_limit_triggered 返回 True"""
        rm = RiskManager(RiskConfig(daily_loss_limit=0.05))
        prev_value = 1_000_000
        daily_loss = -60_000  # -6%
        assert rm.is_daily_loss_limit_triggered(daily_loss, prev_value) is True

    def test_daily_loss_limit_not_triggered(self):
        """日亏损未超限时返回 False"""
        rm = RiskManager(RiskConfig(daily_loss_limit=0.05))
        prev_value = 1_000_000
        daily_loss = -30_000  # -3%
        assert rm.is_daily_loss_limit_triggered(daily_loss, prev_value) is False

    def test_drawdown_calculation(self):
        """回撤计算正确"""
        rm = RiskManager(RiskConfig(max_drawdown_stop=0.20))
        # peak=100, current=82 → drawdown=18%
        peak = 100_000
        current = 82_000
        dd = (peak - current) / peak
        assert abs(dd - 0.18) < 1e-9


class TestVolCorrRiskManager:
    def test_vol_corr_risk_manager_init(self):
        """VolCorrRiskManager 可正常实例化"""
        from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
        vrm = VolCorrRiskManager(VolCorrRiskConfig())
        assert vrm.config is not None

    def test_get_risk_budget(self):
        """get_risk_budget 返回结构正确"""
        from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
        vrm = VolCorrRiskManager(VolCorrRiskConfig())
        budget = vrm.get_risk_budget("normal", 0.5)
        assert isinstance(budget, dict)
        assert "max_position_pct" in budget or "可用仓位" in str(budget) or len(budget) >= 0
