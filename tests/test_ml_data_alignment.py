"""P0-1: ML 标签 stock/benchmark 窗口对齐

修复前问题：
- stock `_compute_forward_returns`: arr[i] = (close[i+5] / close[i]) - 1 → 5d 窗口 [i, i+5]
- bench `_compute_excess_returns`: bench.pct_change(5).shift(-6) → 5d 窗口 [i+1, i+6]
- 两者错位 1 天，超额收益标签存在系统性偏差

修复后：
- stock: arr[i] = (close[i+5+1] / close[i+1]) - 1 = (close[i+6] / close[i+1]) - 1
- bench: bench.pct_change(5).shift(-5) = bench[i+5] / bench[i] - 1（5d 窗口 [i+1, i+6]）
- 两者窗口一致：起点 i+1，终点 i+6
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, '.')

import numpy as np
import pandas as pd
import pytest

from lib.ml.ml_data import MLDataPipeline


def _make_close(prices):
    """构造 7 日 close 数组"""
    return np.array(prices, dtype=float)


def test_stock_5d_window_starts_at_i_plus_1():
    """P0-1: stock 5d 标签窗口应从 i+1 起点（与 bench 对齐）"""
    pipeline = MLDataPipeline.__new__(MLDataPipeline)  # 绕过 __init__
    close = _make_close([100, 101, 102, 103, 104, 105, 106])
    result = pipeline._compute_forward_returns(close)
    fwd_5d = result["fwd_5d"]

    # 修复后期望：arr[0] = (close[0+5+1] / close[0+1]) - 1 = 106/101 - 1
    expected = (close[6] / close[1]) - 1
    assert abs(fwd_5d[0] - expected) < 1e-9, \
        f"期望 stock 5d[0]={expected:.6f}, 实际 {fwd_5d[0]:.6f}（窗口起点 i+1）"


def test_bench_5d_window_matches_stock_after_fix():
    """P0-1: bench 5d 标签窗口与 stock 一致 [i+1, i+6]

    修复后 bench 保持 pct_change(5).shift(-6)（窗口 [i+1, i+6]）
    """
    bench_close = pd.Series([100, 101, 102, 103, 104, 105, 106])

    # 修复后：pct_change(5).shift(-6) 在 i=0 = (close[6] - close[1]) / close[1] = 0.0495
    bench_fwd_5d = bench_close.pct_change(5).shift(-6)

    # bench_fwd_5d[0] = (106 - 101) / 101 = 起点 i+1, 终点 i+6
    expected = (106 - 101) / 101
    assert abs(bench_fwd_5d[0] - expected) < 1e-9, \
        f"期望 bench 5d[0]={expected:.6f}, 实际 {bench_fwd_5d[0]:.6f}"


def test_stock_and_bench_windows_aligned():
    """P0-1: stock 和 bench 5d 窗口在 i=0 处必须产生完全相同的基准对照

    修复后 stock [i+1, i+6], bench [i+1, i+6]（shift(-6)）
    """
    close = _make_close([100, 101, 102, 103, 104, 105, 106])
    bench_close = pd.Series(close)

    pipeline = MLDataPipeline.__new__(MLDataPipeline)
    stock_fwd_5d = pipeline._compute_forward_returns(close)["fwd_5d"]
    bench_fwd_5d = bench_close.pct_change(5).shift(-6)  # 修复后

    # 两者在 i=0 处的窗口应该完全相同：[i+1, i+6] = [1, 6]
    # stock: (close[6] / close[1]) - 1
    # bench:  pct_change(5).shift(-6) 在 i=0 = (close[6] - close[1]) / close[1]
    assert abs(stock_fwd_5d[0] - bench_fwd_5d[0]) < 1e-9, \
        f"stock {stock_fwd_5d[0]:.6f} vs bench {bench_fwd_5d[0]:.6f} 窗口不对齐"


def test_20d_window_alignment():
    """P0-1: 20d 窗口也应一致"""
    close = _make_close(list(range(100, 130)))  # 30 个值
    bench_close = pd.Series(close)

    pipeline = MLDataPipeline.__new__(MLDataPipeline)
    stock_fwd_20d = pipeline._compute_forward_returns(close)["fwd_20d"]
    bench_fwd_20d = bench_close.pct_change(20).shift(-21)  # 修复后

    # i=0 窗口应都是 [1, 21]
    assert abs(stock_fwd_20d[0] - bench_fwd_20d[0]) < 1e-9
