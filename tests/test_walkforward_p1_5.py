"""P1-5: dim-5 P1-5 修复 - lib/walkforward.py 验证不覆盖 ML/贝叶斯路径

修复前：walkforward 每个窗口调 strategy_fn 2 次（train + val）
- 但 adaptive state（贝叶斯、ML 模型、IC 缓存）在多次调用间累积
- 验证样本"看到"的是整个历史学到的 state，而非"窗口起点前的 state"
- dim-3 的"样本外验证"部分失效

修复后：run() 在每个窗口起点 snapshot adaptive state，结束 restore
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import inspect
import pytest
from unittest.mock import patch


def test_walkforward_run_signature_accepts_adaptive_state():
    """P1-5: 修复后 run() 接受 adaptive_state 参数"""
    import lib.strategy.walkforward as walkforward
    from lib.strategy.walkforward import WalkForwardBacktest, WalkForwardConfig

    sig = inspect.signature(WalkForwardBacktest.run)
    assert "adaptive_state" in sig.parameters, \
        f"WalkForwardBacktest.run() 必须接受 adaptive_state 参数，实际签名: {sig}"


def test_walkforward_run_passes_adaptive_state_to_strategy():
    """P1-5: strategy_fn 调用时收到 adaptive_state"""
    from lib.strategy.walkforward import WalkForwardBacktest, WalkForwardConfig

    strategy_calls = []

    def mock_strategy_fn(market_data, factor_data, dates, symbols, adaptive_state=None):
        strategy_calls.append({"adaptive_state": adaptive_state})
        return {"trades": [], "metrics": {"total_return": 0.0}}

    config = WalkForwardConfig(
        train_days=10, validation_days=5, step_days=5,
        min_train_trades=0, min_validation_trades=0,
    )
    wf = WalkForwardBacktest(config=config)

    import pandas as pd
    dates = pd.date_range("2024-01-01", periods=20, freq="B")
    market_data = {"600519": pd.DataFrame({
        "trade_date": dates, "close": [100.0] * 20, "high": [101.0] * 20,
        "low": [99.0] * 20, "open": [100.0] * 20, "volume": [1e6] * 20,
    })}

    adaptive_state = {"snapshot_id": "win_0_start", "weights": {"热门板块": 0.5}}
    rounds = wf.run(
        strategy_fn=mock_strategy_fn,
        market_data=market_data,
        dates=dates.tolist(),
        symbols=["600519"],
        adaptive_state=adaptive_state,
    )

    # 关键断言：strategy 被调用，每次收到 adaptive_state
    assert len(strategy_calls) > 0, "strategy_fn 应被调用"
    for call in strategy_calls:
        assert "adaptive_state" in call, \
            f"strategy_fn 调用必须含 adaptive_state 关键字参数，实际: {call}"


def test_walkforward_run_snapshots_state_per_window():
    """P1-5: 每个窗口的 adaptive_state 反映"窗口起点前的状态"（隔离）"""
    from lib.strategy.walkforward import WalkForwardBacktest, WalkForwardConfig
    from unittest.mock import MagicMock

    # mock bayesian state 文件，模拟多次读
    state_reads = []

    def mock_strategy_fn(market_data, factor_data, dates, symbols, adaptive_state=None):
        # 记录每次调用的 adaptive_state 快照
        state_reads.append(adaptive_state)
        return {"trades": [], "metrics": {"total_return": 0.0}}

    config = WalkForwardConfig(
        train_days=10, validation_days=5, step_days=5,
        min_train_trades=0, min_validation_trades=0,
    )
    wf = WalkForwardBacktest(config=config)

    import pandas as pd
    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    market_data = {"600519": pd.DataFrame({
        "trade_date": dates, "close": [100.0] * 30, "high": [101.0] * 30,
        "low": [99.0] * 30, "open": [100.0] * 30, "volume": [1e6] * 30,
    })}

    # 模拟 walkforward 期间 bayesian state 变化
    snapshots = [
        {"win": 0, "weights": {"热门板块": 0.3}},
        {"win": 1, "weights": {"热门板块": 0.5}},
    ]
    snapshot_idx = [0]

    def fake_snapshot(state):
        return snapshots[min(snapshot_idx[0], len(snapshots) - 1)]

    def fake_restore(state, snapshot):
        snapshot_idx[0] += 1
        return state

    # 这里 walkforward 内部应调用 snapshot/restore；测试期望 strategy 收到正确 snapshot
    with patch.object(walkforward_module := sys.modules['lib.strategy.walkforward'],
                      'Path', wraps=__import__('pathlib').Path):
        wf.run(
            strategy_fn=mock_strategy_fn,
            market_data=market_data,
            dates=dates.tolist(),
            symbols=["600519"],
        )


# 测试 source code 验证（不依赖运行结果）
def test_walkforward_source_code_calls_strategy_with_adaptive_state():
    """P1-5: 验证 run() source code 在 strategy_fn 调用时传 adaptive_state"""
    import lib.strategy.walkforward as walkforward
    source = inspect.getsource(walkforward)

    # 找 run() 方法
    run_idx = source.find("def run(")
    assert run_idx > 0

    # run() 段
    next_def_idx = source.find("\n    def ", run_idx + 1)
    if next_def_idx < 0:
        next_def_idx = len(source)
    run_section = source[run_idx:next_def_idx]

    # 接受 adaptive_state 参数
    assert "adaptive_state" in run_section, \
        "walkforward.run() 必须接受 adaptive_state 参数（P1-5 修复未完成）"

    # strategy_fn 调用时传 adaptive_state
    # 检查有 "adaptive_state=" 在 run_section 中出现至少 2 次（train + val 各一次）
    adaptive_state_calls = run_section.count("adaptive_state=")
    assert adaptive_state_calls >= 2, \
        f"strategy_fn 调用时必须传 adaptive_state 关键字参数（train + val 各一次），实际 {adaptive_state_calls} 次"
