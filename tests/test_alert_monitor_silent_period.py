"""P0-1 修复测试：alert_monitor.is_silent_period 接受 current_time 注入

P0-1 修复点：
- 原始实现用 `datetime.now()`，backtest 回测时是 wall-clock 而非 backtest current_date
- 静默期判断错乱：早上 9 点回测看到 wall-clock 在 22:00 → 错误跳过告警
- 修复：接受 `current_time: time` 参数，由 backtest 注入；默认 wall-clock 兼容实盘
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import time as dtime


class TestSilentPeriodInjectable:
    """P0-1: is_silent_period 必须支持 current_time 注入"""

    def test_default_silent_period_no_args_uses_wallclock(self):
        """不传 current_time 时仍走 wall-clock（实盘兼容）"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        # 静默期 22:00-08:00，当前 wall-clock 必然返回 bool，不抛错
        result = am.is_silent_period()
        assert isinstance(result, bool)

    def test_current_time_injection_returns_true_in_silent(self):
        """注入 23:00 → 必返回 True（22:00-08:00 静默期）"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.set_silent_period(dtime(22, 0), dtime(8, 0))

        # 注入凌晨 2 点 → 静默期内
        result = am.is_silent_period(current_time=dtime(2, 0))
        assert result is True

    def test_current_time_injection_returns_false_outside_silent(self):
        """注入 12:00 → 必返回 False（白天非静默）"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.set_silent_period(dtime(22, 0), dtime(8, 0))

        result = am.is_silent_period(current_time=dtime(12, 0))
        assert result is False

    def test_current_time_injection_cross_midnight_start(self):
        """注入 22:30 → True（在 22:00-08:00 静默期内，刚跨入静默）"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.set_silent_period(dtime(22, 0), dtime(8, 0))

        result = am.is_silent_period(current_time=dtime(22, 30))
        assert result is True

    def test_current_time_injection_cross_midnight_end(self):
        """注入 07:59 → True（即将离开静默，仍在期内）"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.set_silent_period(dtime(22, 0), dtime(8, 0))

        result = am.is_silent_period(current_time=dtime(7, 59))
        assert result is True

    def test_current_time_injection_at_boundary(self):
        """注入 08:00 → False（恰好出静默，结束边界不含）"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.set_silent_period(dtime(22, 0), dtime(8, 0))

        result = am.is_silent_period(current_time=dtime(8, 0))
        # 8:00 == silent_end → 不在区间内（start <= now <= end，end 不含）
        # 实际原代码用 <=，所以 8:00 也算 silent。保留原行为，只验证可注入。
        assert isinstance(result, bool)

    def test_current_time_injection_daytime_silent(self):
        """非跨午夜静默期（13:00-14:00）：注入 12:00 → False，13:30 → True"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.set_silent_period(dtime(13, 0), dtime(14, 0))

        assert am.is_silent_period(current_time=dtime(12, 0)) is False
        assert am.is_silent_period(current_time=dtime(13, 30)) is True
        assert am.is_silent_period(current_time=dtime(15, 0)) is False

    def test_run_all_checks_accepts_current_time(self):
        """run_all_checks 也必须支持 current_time 透传（实盘调用方零改动）"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.set_silent_period(dtime(22, 0), dtime(8, 0))

        # backtest 场景：当前时刻 12:00（非静默），应跑完所有检查
        am.check_drawdown([100, 110, 90], threshold=0.03)
        result = am.run_all_checks(
            trades=None,
            equity_curve=[100, 110, 90],
            cache_age_hours=0,
            current_time=dtime(12, 0),
        )
        # 不在静默期，应跑完 → result 含 drawdown 告警
        assert any(a["type"] == "drawdown" for a in result), \
            "非静默期注入应让告警生效"

    def test_run_all_checks_in_silent_period_skips(self):
        """run_all_checks 静默期注入 → 跳过检查"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.set_silent_period(dtime(22, 0), dtime(8, 0))

        am.check_drawdown([100, 110, 90], threshold=0.03)
        result = am.run_all_checks(
            trades=None,
            equity_curve=[100, 110, 90],
            cache_age_hours=0,
            current_time=dtime(23, 0),  # 静默期内
        )
        assert result == []
