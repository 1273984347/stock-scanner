"""A/B 测试框架测试 —— Phase 3 统一入口改造后新增。

背景（2026-11-07）：`lib/strategy/ab_testing.py` 原先 import 的
`backtest.engine.BacktestEngine` **在 backtest/engine.py 中并不存在**
⇒ ImportError 被 `try/except` 静默吞掉 ⇒ **真实回测从未参与 A/B 测试**，
一直在跑 mock（mock 自己的注释写着「跨 session 不可复现」）。

Phase 3 改为走统一入口 `backtest.unified`，本测试固化该行为：
1. 统一入口可用时 **不得** 走 mock；
2. 统一入口不可用时才降级到 mock（保留降级能力）；
3. `run_test` 能产出完整 ABTestResult。
"""

import sys
import pytest

sys.path.insert(0, "D:/stock-scanner")

from lib.strategy.ab_testing import ABTester, ABTestConfig, ABTestResult


class TestABTesterEngineWiring:
    """引擎接线：不得静默降级到 mock"""

    def test_uses_unified_engine_not_mock(self):
        """统一入口可用时，必须用统一入口（而非 mock）"""
        tester = ABTester()
        engine = tester._get_backtest_engine()
        # mock 引擎是内嵌类，类名固定为 MockEngine
        assert engine.__class__.__name__ != "MockEngine", (
            "BacktestEngine 不存在导致静默降级到 mock —— 这是 Phase 3 修掉的缺陷"
        )

    def test_engine_is_cached(self):
        """引擎应被缓存，不重复构造"""
        tester = ABTester()
        assert tester._get_backtest_engine() is tester._get_backtest_engine()

    def test_unified_engine_is_used_when_available(self, monkeypatch):
        """显式验证：统一入口的 run 结果被正确适配成 A/B 需要的字段"""
        import backtest.unified as unified_mod

        class _FakeUnified:
            mode = "simple"
            trade_count = 3
            raw = {
                "metrics": {"total_return": 0.12, "sharpe": 1.5,
                           "max_drawdown": -0.08, "win_rate": 0.6,
                           "total_trades": 3},
                "trades": [{"code": "600519"}],
                "equity_curve": [("2026-01-01", 100.0), ("2026-01-02", 101.0),
                                ("2026-01-03", 102.0)],
            }

        monkeypatch.setattr(unified_mod.BacktestRunner, "run",
                            lambda self, **kw: _FakeUnified())
        tester = ABTester()
        out = tester._get_backtest_engine().run(["600519"], 100, {"x": 1})
        assert out["total_return"] == 0.12
        assert out["sharpe_ratio"] == 1.5
        assert out["trades"] == 3
        # 日收益序列由 equity_curve 推导
        assert len(out["returns"]) == 2


class TestABTesterRun:
    """A/B 测试主流程"""

    def test_run_test_produces_result(self, monkeypatch):
        """两组参数跑出可用的 ABTestResult"""
        tester = ABTester()

        class _FakeEngine:
            def __init__(self):
                self._n = 0

            def run(self, symbols, days, params=None):
                self._n += 1
                # A 组收益低、B 组高 ⇒ B 胜出
                base = 0.001 if (params or {}).get("fast") else 0.0002
                n = 120
                returns = [base] * n
                total = (1 + base) ** n - 1
                return {
                    "total_return": total,
                    "sharpe_ratio": base * 252 ** 0.5,
                    "max_drawdown": -0.01,
                    "win_rate": 0.9,
                    "trades": 30,
                    "returns": returns,
                }

        monkeypatch.setattr(tester, "_get_backtest_engine", lambda: _FakeEngine())
        cfg = ABTestConfig(symbols=["600519"], days=120,
                           group_a={"fast": False}, group_b={"fast": True})
        res = tester.run_test(cfg)
        assert isinstance(res, ABTestResult)
        assert res.winner in ("A", "B", "无显著差异")
        assert res.a_trades == 30 and res.b_trades == 30

    def test_run_test_with_insufficient_data(self, monkeypatch):
        """数据不足时应优雅处理而不是崩溃"""
        tester = ABTester()

        class _EmptyEngine:
            def run(self, symbols, days, params=None):
                return {"total_return": 0, "sharpe_ratio": 0, "max_drawdown": 0,
                        "win_rate": 0, "trades": 0, "returns": []}

        monkeypatch.setattr(tester, "_get_backtest_engine", lambda: _EmptyEngine())
        cfg = ABTestConfig(symbols=["600519"], days=10)
        res = tester.run_test(cfg)
        assert isinstance(res, ABTestResult)