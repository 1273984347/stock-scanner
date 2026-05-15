"""仓位管理模块单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd
from lib.position_manager import PositionManager, Position


class TestPositionManager:
    def test_position_manager_init(self, tmp_path):
        """PositionManager 可正常实例化"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        assert isinstance(pm.positions, dict)
        assert len(pm.positions) == 0

    def test_track_position(self, tmp_path):
        """添加持仓"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        pm.track_position(code="000001", name="平安银行", buy_price=12.50,
                           shares=1000)
        assert "000001" in pm.positions
        assert pm.positions["000001"].shares == 1000

    def test_remove_position(self, tmp_path):
        """移除持仓"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        pm.track_position(code="000001", name="平安银行", buy_price=12.50,
                           shares=1000)
        pm.remove_position("000001")
        assert "000001" not in pm.positions

    def test_get_portfolio_summary(self, tmp_path):
        """组合汇总返回结构正确"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        pm.track_position(code="000001", name="平安银行", buy_price=12.50,
                           shares=1000)
        summary = pm.get_portfolio_summary(current_prices={"000001": 13.00})
        assert isinstance(summary, dict)
        # 总市值 = 1000 * 13 = 13000
        assert summary.get("total_value", 0) == 13_000

    def test_check_stop_loss(self, tmp_path):
        """止损检查返回结构正确"""
        pm = PositionManager(positions_file=str(tmp_path / "positions.json"))
        pm.track_position(code="000001", name="平安银行", buy_price=12.50,
                           shares=1000, stop_loss=0.05)
        alerts = pm.check_stop_loss(current_prices={"000001": 11.50})
        assert isinstance(alerts, list)
        assert len(alerts) == 1
        assert alerts[0]["action"] == "止损"
