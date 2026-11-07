"""P0-4: lib/walkforward.py create_windows 末尾窗口可能漏掉

修复前: range(0, n - train_bars - val_bars + 1, step_bars)
        最后窗口可能漏掉当 (n - train_bars - val_bars) % step_bars != 0
修复后: 末尾追加补救窗口（如 n=30, train=20, val=5, step=20 → 漏掉 [25,29]）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd

from lib.strategy.walkforward import WalkForwardBacktest, WalkForwardConfig


def test_p0_4_includes_last_window_when_step_overshoots():
    """P0-4: n=30, train=20, val=5, step=20 → 应包含 [25, 29] 末尾窗口

    range 上限: 0..5 (step=20) → 只有 start=0 一个窗口
    末尾补救: start=25, train=[25,44]？等等 train_end=44 > n-1=29
    实际补救逻辑: start=20, train=[20,39]? train_end=39 > 29
    正确补救: 找到能放下 train+val 的最后一个 start
    """
    config = WalkForwardConfig(
        train_days=20,
        validation_days=5,
        step_days=20,
    )
    wf = WalkForwardBacktest(config=config)

    # 30 个日期, train=20 + val=5 = 25 → 应能放下至少 1 个完整窗口
    dates = pd.date_range("2024-01-01", periods=30, freq="B").strftime("%Y-%m-%d").tolist()

    windows = wf.create_windows(dates=dates)

    # 至少应有 1 个窗口
    assert len(windows) >= 1, f"P0-4: 应至少有 1 个窗口, 实际 0"

    # 最后一个窗口的 validation_end 应是数据末尾
    last = windows[-1]
    assert last.validation_end == 29, (
        f"P0-4 修复未完成: 末窗 validation_end 应为 29, "
        f"实际: {last.validation_end}。原代码用 range step=20 在 n=30 时漏掉末尾窗口"
    )


def test_p0_4_handles_step_exact_multiple():
    """P0-4: n=45, train=20, val=5, step=20 → range 正好对齐不漏"""
    config = WalkForwardConfig(
        train_days=20,
        validation_days=5,
        step_days=20,
    )
    wf = WalkForwardBacktest(config=config)

    # n=45, step=20, 上限=20: range(0, 21, 20) = [0, 20] → 2 个窗口 (start=0, start=20)
    # 末窗 start=20, train=[20,39], val=[40,44], val_end=44=n-1=44
    dates = pd.date_range("2024-01-01", periods=45, freq="B").strftime("%Y-%m-%d").tolist()

    windows = wf.create_windows(dates=dates)

    assert len(windows) == 2, f"P0-4: 应有 2 个窗口, 实际 {len(windows)}"
    assert windows[-1].validation_end == 44, (
        f"P0-4 修复未完成: 末窗 validation_end 应为 44, 实际: {windows[-1].validation_end}"
    )


def test_p0_4_does_not_overshoot():
    """P0-4: 末窗的 train 也不能超出数据范围"""
    config = WalkForwardConfig(
        train_days=20,
        validation_days=5,
        step_days=20,
    )
    wf = WalkForwardBacktest(config=config)

    # 33 个日期, 末窗补救: start 应 ≤ n - train - val = 8
    dates = pd.date_range("2024-01-01", periods=33, freq="B").strftime("%Y-%m-%d").tolist()

    windows = wf.create_windows(dates=dates)
    # 末窗 start 应 ≤ 8, train_end ≤ 27, val_end = min(32, 32) = 32
    last = windows[-1]
    assert last.train_end <= 32, f"train_end {last.train_end} 超出数据"
    assert last.validation_end == 32, (
        f"P0-4 修复未完成: 末窗 validation_end 应为 32, 实际: {last.validation_end}"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
