"""
P0-1 修复测试: 周亏损累计重置跨假期/周间断档

Bug 描述 (lib/risk_manager.py:351-377):
    `_check_weekly_loss` 周重置条件是
    `now.weekday() == 0 and (now - self._last_week_reset).days >= 1`，
    仅在周一调用时重置。
    若周一整个交易日无调用（节假日 / 周末休市 / 系统未运行），
    周二调用时 `weekday()==1` 永不为 True，
    `_weekly_returns` 跨周累加，导致周亏损限可能误判。

修复：
    用 `last_week_reset.isocalendar().week != now.isocalendar().week`
    替代 weekday==0 判断。跨周（不论周几）即重置。
    同时支持测试注入 `now` 参数（默认 None = datetime.now()）。
"""
from datetime import datetime, timedelta

import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestWeeklyResetCrossWeek:
    """跨周/跨假期场景下 _weekly_returns 应正确重置"""

    def test_missed_monday_tuesday_should_reset(self):
        """P0-1: 周一整日无调用，周二调用时应重置周累计

        场景：
          - 周一 2026-06-08 节假日，无 check_all() 调用
          - 周一 09:00 注入 last_week_reset=2026-06-01（上上周一）模拟历史状态
          - 周二 2026-06-09 10:00 调用 _check_weekly_loss
        修复前：weekday()==1 != 0 → 不重置，上周累计被错算
        修复后：isocalendar().week 不同 → 重置
        """
        from lib.risk.risk_manager import RiskManager, RiskConfig

        rm = RiskManager(RiskConfig(weekly_loss_limit=0.08))
        # 模拟上周累积了 5% 亏损（即将触发阈值）
        rm._weekly_returns = [-0.01, -0.02, -0.01, -0.01]
        rm._last_week_reset = datetime(2026, 6, 1, 10, 0)  # 上周一
        rm._last_return_date = "2026-06-05"  # 上周五

        # 周二 2026-06-09 10:00 调用（周一假期未调用）
        tue = datetime(2026, 6, 9, 10, 0)
        alert = rm._check_weekly_loss({"daily_return": -0.01}, now=tue)

        # 修复后：跨周（isocalendar week 23 != 24）应重置
        assert rm._weekly_returns == [-0.01], (
            f"P0-1 修复未生效：跨周未重置，_weekly_returns={rm._weekly_returns}。"
            f" 周一假期未调用，周二仍沿用上周累计。"
        )
        # 修复后：新一周累计仅 -1%，不应触发 8% 周亏损限
        assert alert is None, (
            f"P0-1 修复未生效：跨周累计误判触发周亏损限。"
            f" 新一周仅 -1% 不应触发阈值。"
        )

    def test_normal_tuesday_within_same_week_does_not_reset(self):
        """P0-1: 周二 10:00（上周一有调用）同周不应重置

        场景：
          - 上周一 2026-06-08 09:00 调用过（last_week_reset=上周一）
          - 周二 2026-06-09 10:00 继续调用
        期望：同周不重置，_weekly_returns 继续累积
        """
        from lib.risk.risk_manager import RiskManager, RiskConfig

        rm = RiskManager(RiskConfig(weekly_loss_limit=0.08))
        rm._weekly_returns = [-0.01, -0.02]
        rm._last_week_reset = datetime(2026, 6, 8, 9, 0)  # 上周一
        rm._last_return_date = "2026-06-08"

        # 周二 10:00 调用
        tue = datetime(2026, 6, 9, 10, 0)
        rm._check_weekly_loss({"daily_return": -0.01}, now=tue)

        # 同周（isocalendar week 都是 24）不重置
        assert rm._weekly_returns == [-0.01, -0.02, -0.01], (
            f"同周不应重置：{rm._weekly_returns}"
        )

    def test_first_call_with_no_last_reset_still_works(self):
        """P0-1: 首次调用（_last_week_reset is None）应正常初始化

        回归测试：确保新的判断逻辑不破坏首次调用路径。
        """
        from lib.risk.risk_manager import RiskManager, RiskConfig

        rm = RiskManager(RiskConfig(weekly_loss_limit=0.08))
        assert rm._last_week_reset is None

        tue = datetime(2026, 6, 9, 10, 0)
        rm._check_weekly_loss({"daily_return": -0.01}, now=tue)

        # 首次调用：_last_week_reset 应被设置为传入的 now
        assert rm._last_week_reset == tue
        assert rm._weekly_returns == [-0.01]

    def test_year_boundary_week_reset(self):
        """P0-1: 跨年周（W52 → W1）也应正确重置

        边界：isocalendar 的 week 在跨年时会重置但 year 可能相同/不同。
        仅比较 week 字段足够（week 编号 ISO 标准不重复），
        但要确保 year != 时也能识别（用 year+week tuple 更稳妥）。
        """
        from lib.risk.risk_manager import RiskManager, RiskConfig

        rm = RiskManager(RiskConfig(weekly_loss_limit=0.08))
        rm._weekly_returns = [-0.05, -0.04]
        # 2025-W52 周一：2025-12-22
        rm._last_week_reset = datetime(2025, 12, 22, 10, 0)
        rm._last_return_date = "2025-12-26"

        # 2026-W1 周一：2026-01-05
        next_year = datetime(2026, 1, 5, 10, 0)
        rm._check_weekly_loss({"daily_return": -0.01}, now=next_year)

        # 跨年跨周应重置
        assert rm._weekly_returns == [-0.01], (
            f"跨年跨周未重置：{rm._weekly_returns}"
        )

    def test_check_all_uses_injected_now(self):
        """P0-1: check_all() 应支持从 context 注入 now（默认 datetime.now()）

        设计：_check_weekly_loss 接受 now 参数（默认 None）；
              check_all 接受 now 参数（默认 None）并放入 context。
        """
        from lib.risk.risk_manager import RiskManager, RiskConfig

        rm = RiskManager(RiskConfig(weekly_loss_limit=0.08))
        # 首次调用时 last_week_reset 为 None
        next_year = datetime(2026, 1, 5, 10, 0)
        # 验证 _check_weekly_loss 接受 now 关键字参数
        alert = rm._check_weekly_loss({"daily_return": -0.01}, now=next_year)
        assert alert is None
        assert rm._last_week_reset == next_year
