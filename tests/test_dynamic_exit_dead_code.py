"""P0-2 修复测试：DynamicLossBreaker 是死代码 → 选择方案 (b) 删除

P0-2 决策: 选项 (b) — 删除 `DynamicLossBreaker` 只留 `ConsecutiveLossBreaker`

理由：
1. `backtest/engine.py` 已用 `ConsecutiveLossBreaker`（line 1210），从未实例化 `DynamicLossBreaker`
2. `DynamicLossBreaker` 仅有 `test_new_modules.py` 测试 — 纯死代码
3. 实盘路径（`paper_trading_runner.py` / `ml_backtest.py`）若需要 regime-aware
   连亏熔断，应改造 `ConsecutiveLossBreaker`（统一一套实现），而非保留两套
4. 删除后 `DynamicExitManager` 保留 regime→exit_days 映射（`get_exit_days()`）
   和 VolatilityPositionSizer/DynamicTakeProfit（仍有真实价值）
5. 减 ~80 行死代码 + 减 6 个死测试 + 减 V6 设计债
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


class TestDynamicLossBreakerRemoved:
    """P0-2: DynamicLossBreaker 必须从 dynamic_exit 删除（方案 b）"""

    def test_dynamic_loss_breaker_class_removed(self):
        """DynamicLossBreaker 不再 import 得"""
        import lib.risk.dynamic_exit as dynamic_exit
        assert not hasattr(dynamic_exit, "DynamicLossBreaker"), \
            "DynamicLossBreaker 是死代码（backtest 从未实例化），必须删除"

    def test_loss_breaker_config_removed(self):
        """LossBreakerConfig 也应删除（仅被 DynamicLossBreaker 使用）"""
        import lib.risk.dynamic_exit as dynamic_exit
        assert not hasattr(dynamic_exit, "LossBreakerConfig"), \
            "LossBreakerConfig 仅服务于 DynamicLossBreaker，应一并删除"

    def test_dynamic_exit_manager_uses_consecutive_breaker(self):
        """DynamicExitManager.loss_breaker 必须是 ConsecutiveLossBreaker 实例"""
        from lib.risk.dynamic_exit import DynamicExitManager
        from lib.risk.partial_exit import ConsecutiveLossBreaker
        mgr = DynamicExitManager()
        assert isinstance(mgr.loss_breaker, ConsecutiveLossBreaker), \
            "DynamicExitManager 应复用 ConsecutiveLossBreaker（partial_exit.py）"

    def test_dynamic_exit_manager_still_has_exit_days(self):
        """regime → exit_days 映射仍存在（V6 设计保留）"""
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()
        mgr.update_market_state("TREND_UP", 0.25)
        assert mgr.get_exit_days() == 20, "TREND_UP → 20 天"
        mgr.update_market_state("MEAN_REVERSION", 0.25)
        assert mgr.get_exit_days() == 10, "MEAN_REVERSION → 10 天"

    def test_dynamic_exit_manager_vol_sizer_still_works(self):
        """VolatilityPositionSizer 保留（真实使用：动态仓位）"""
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()
        mgr.update_market_state("MEAN_REVERSION", 0.15)
        # 低波动 → 仓位更大
        pct = mgr.get_position_pct()
        assert pct > 0, "position_pct 必须 > 0（无熔断时）"

    def test_dynamic_take_profit_still_works(self):
        """DynamicTakeProfit 保留（真实使用：动态止盈）"""
        from lib.risk.dynamic_exit import DynamicTakeProfit
        tp = DynamicTakeProfit()
        low_vol = tp.get_exit_levels(0.15)
        high_vol = tp.get_exit_levels(0.50)
        # 高波动时止盈目标更低
        assert high_vol[0][0] < low_vol[0][0]
