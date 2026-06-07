"""P0-3: lib/walkforward.py adaptive_state 深拷贝防 train/val mutation 泄漏

修复前: dict(adaptive_state) 是浅拷贝, values 仍是引用
        → train 阶段 strategy_fn 内部若改 list/dict 嵌套 state, 泄漏到 val 阶段
修复后: copy.deepcopy(adaptive_state) 完整隔离
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import copy

from lib.walkforward import WalkForwardBacktest, WalkForwardConfig


def _make_data(n=80, code="600519"):
    """构造 n 天的市场数据"""
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return {
        code: pd.DataFrame({
            "trade_date": dates,
            "close": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "open": [100.0] * n,
            "volume": [1e6] * n,
        })
    }, [str(d.date()) for d in dates]


def test_p0_3_adaptive_state_deepcopy_isolates_writes():
    """P0-3: train 阶段对 adaptive_state 嵌套值的修改不应泄漏到 val 阶段"""
    received_states = []

    def strategy_fn(market_data, factor_data, dates, symbols, adaptive_state=None, **kwargs):
        # 关键: 在 mutation 之前记录 state (snapshot 进函数时的状态)
        received_states.append(copy.deepcopy(adaptive_state))
        # 模拟 strategy_fn 内部会修改 state 嵌套值
        if adaptive_state is not None:
            # 嵌套 list 操作
            if "weights_history" not in adaptive_state:
                adaptive_state["weights_history"] = []
            adaptive_state["weights_history"].append(len(dates))
            # 嵌套 dict 操作
            if "nested" not in adaptive_state:
                adaptive_state["nested"] = {"counter": 0}
            adaptive_state["nested"]["counter"] += 1
        return {"trades": [], "total_return": 0, "total_trades": 0}

    config = WalkForwardConfig(
        train_days=20,
        validation_days=5,
        step_days=5,
        min_train_trades=0,
        min_validation_trades=0,
    )
    wf = WalkForwardBacktest(config=config)
    market_data, dates = _make_data(n=80)

    initial_state = {"weights": {"热门板块": 0.5}, "nested": {"counter": 0}}
    wf.run(
        strategy_fn=strategy_fn,
        market_data=market_data,
        dates=dates,
        symbols=["600519"],
        adaptive_state=initial_state,
    )

    # 至少 2 次调用 (train + val)
    assert len(received_states) >= 2, f"应至少有 2 次调用, 实际 {len(received_states)}"

    # 关键断言: 每个窗口的 train 和 val 看到的初始 state 应一致 (没有 mutation 泄漏)
    # 调用顺序: train_0, val_0, train_1, val_1, ...
    # train_0 和 val_0 看到的初始 weights_history 都应该是 [] (空)
    # 因为每次 strategy_fn 调用后, weights_history 应该被 reset (来自初始 snapshot)

    # 第一次调用 (train_0) 应该是干净的 initial_state
    first = received_states[0]
    assert first.get("weights", {}).get("热门板块") == 0.5, (
        f"第 1 次调用 state 应保持初始 weights=0.5, 实际: {first.get('weights')}"
    )
    # 第二次调用 (val_0) 看到的 state 应与 train_0 的初始状态一致
    # 也就是说 val_0 不应看到 train_0 写入的 weights_history=[20]
    second = received_states[1]
    assert "weights_history" not in second, (
        f"P0-3 修复未完成: 第 2 次调用 (val_0) 看到 weights_history={second.get('weights_history')}, "
        f"应是 train 阶段的 mutation 泄漏。原代码用 dict() 浅拷贝, nested list 仍是引用"
    )


def test_p0_3_initial_state_not_mutated():
    """P0-3: 外部传入的 adaptive_state 不应被 run() 改动"""
    initial_state = {
        "weights": {"A": 0.5},
        "history": [],
    }
    state_copy = copy.deepcopy(initial_state)

    def strategy_fn(market_data, factor_data, dates, symbols, adaptive_state=None, **kwargs):
        # 模拟 strategy_fn 写入
        if adaptive_state is not None:
            adaptive_state.setdefault("history", []).append(len(dates))
        return {"trades": [], "total_return": 0, "total_trades": 0}

    config = WalkForwardConfig(
        train_days=20, validation_days=5, step_days=5,
        min_train_trades=0, min_validation_trades=0,
    )
    wf = WalkForwardBacktest(config=config)
    market_data, dates = _make_data(n=80)
    wf.run(
        strategy_fn=strategy_fn,
        market_data=market_data,
        dates=dates,
        symbols=["600519"],
        adaptive_state=initial_state,
    )

    # initial_state 本身不应被改
    assert initial_state == state_copy, (
        f"P0-3 修复未完成: initial_state 被改动: {initial_state} vs {state_copy}。"
        f"run() 必须用 deepcopy 隔离外部 state"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
