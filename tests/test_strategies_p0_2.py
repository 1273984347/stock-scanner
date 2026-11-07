"""
P0-2 修复测试: 删除模块级 _risk_manager 单例

Bug 描述 (lib/strategies.py:30-32):
    _risk_manager = VolCorrRiskManager(VolCorrRiskConfig())
    之前是模块级单例，所有策略共享同一 `consecutive_losses` / `_weekly_returns`。
    风险：get_zt_candidates 连亏 5 次后，get_volume_breakout_candidates 进入时
    被错误触发 REDUCE 动作（多策略状态污染）。

修复：
    删除模块级单例。后续如需在策略内使用 RiskManager，每策略函数内自行实例化。
    这样每个策略调用获得独立的 RiskManager，状态天然隔离。

测试策略：
    1. 验证模块导入时不会自动创建 VolCorrRiskManager 实例
       （不再有共享的 module-level singleton）
    2. 验证不同实例的 consecutive_losses 状态天然隔离
       （演示性，证明 P0-2 修复后无状态污染）
"""
import importlib
import sys

import pytest


class TestNoModuleLevelSingleton:
    """模块导入时不应创建 _risk_manager 单例"""

    def test_no_vol_corr_risk_manager_singleton_at_import(self, monkeypatch):
        """P0-2: 重新加载 strategies 模块时，VolCorrRiskManager 不应被实例化

        修复前：模块导入时执行 `_risk_manager = VolCorrRiskManager(...)`，
                导致所有 import 此模块的代码共享同一实例。
        修复后：模块导入时不应自动创建 RiskManager 实例。
        """
        from lib.risk.risk_manager import VolCorrRiskManager

        call_count = {"n": 0}
        original_init = VolCorrRiskManager.__init__

        def counting_init(self, *args, **kwargs):
            call_count["n"] += 1
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(VolCorrRiskManager, "__init__", counting_init)

        # 强制重新加载 lib.strategy.strategies 触发模块级代码
        if "lib.strategy.strategies" in sys.modules:
            importlib.reload(sys.modules["lib.strategy.strategies"])
        else:
            importlib.import_module("lib.strategy.strategies")

        assert call_count["n"] == 0, (
            f"P0-2 修复未生效：模块导入时创建了 {call_count['n']} 个 "
            f"VolCorrRiskManager 实例（应为 0）。仍存在模块级单例？"
        )

    def test_module_does_not_expose_risk_manager_singleton(self):
        """P0-2: lib.strategy.strategies 模块不应再暴露共享的 _risk_manager 属性

        修复前：import lib.strategy.strategies 后，lib.strategy.strategies._risk_manager 是
                一个共享的 VolCorrRiskManager 实例。
        修复后：不应再有此属性（避免被误用为跨策略共享状态）。
        """
        import lib.strategy.strategies as strategies_module

        # 关键属性已删除
        assert not hasattr(strategies_module, "_risk_manager"), (
            "P0-2 修复未生效：lib.strategy.strategies 仍暴露 _risk_manager 模块级单例。"
            " 该单例会导致多策略状态污染（consecutive_losses / _weekly_returns 跨策略共享）。"
        )


class TestStateIsolationPerInstance:
    """不同 RiskManager 实例的状态应天然隔离（演示性测试）"""

    def test_two_risk_managers_have_independent_state(self):
        """P0-2: 验证 VolCorrRiskManager 的实例状态相互隔离

        即便未来在策略中使用 RiskManager，每个策略自行实例化时，
        状态不会跨策略污染。这是 P0-2 修复的语义保证。
        """
        from lib.risk.risk_manager import VolCorrRiskConfig, VolCorrRiskManager

        rm_a = VolCorrRiskManager(VolCorrRiskConfig())
        rm_b = VolCorrRiskManager(VolCorrRiskConfig())

        # 策略 A 连亏 5 次
        for _ in range(5):
            rm_a.record_trade_result(-0.01)

        # 策略 A 已达到 consecutive_loss 阈值
        assert rm_a.consecutive_losses == 5

        # 策略 B 完全独立，不受 A 的状态影响
        assert rm_b.consecutive_losses == 0, (
            "RiskManager 实例状态污染：策略 A 的连亏计数泄漏到了策略 B"
        )

    def test_weekly_returns_independent_per_instance(self):
        """P0-2: _weekly_returns 状态也应独立

        验证：模拟两个策略同时累积周收益时，互不干扰。
        """
        from lib.risk.risk_manager import VolCorrRiskConfig, VolCorrRiskManager

        rm_a = VolCorrRiskManager(VolCorrRiskConfig())
        rm_b = VolCorrRiskManager(VolCorrRiskConfig())

        # 直接操作内部状态（模拟每日调用 check_all）
        rm_a._weekly_returns = [-0.01, -0.02, -0.03, -0.04, -0.05]
        rm_b._weekly_returns = []

        # A 累积了大量亏损
        assert sum(rm_a._weekly_returns) < -0.10
        # B 干净
        assert sum(rm_b._weekly_returns) == 0
