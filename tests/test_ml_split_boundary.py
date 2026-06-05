"""P1-3: dim-5 P1-3 修复 - lib/ml_data.py 训练/验证/测试段标签跨段污染

修复前：split 只对日期切片，不考虑 fwd_Nd 标签用 [D, D+N] 窗口
- 训练集最末日 D_train_end 的 fwd_5d 标签使用到 D_train_end+5 收益
- D_train_end+5 ∈ val 区间 → 训练集"幽灵"样本用 val 数据标签

修复后：split 时对每段，剔除该段最后 N 天的样本（其 fwd_Nd 标签越界到下一段）
- 实际效果：训练集最末 5 个 B 频率日的样本被剔除
- 验证集最末 5 个 B 频率日的样本被剔除
"""
import os, sys
sys.path.insert(0, '.')

import tempfile
from pathlib import Path

import pandas as pd
import numpy as np
import pytest

from lib.ml_data import MLDataPipeline


def _make_panel_with_forward_returns(n_dates=30, n_stocks=1):
    """构造 panel with fwd_5d/10d/20d 标签

    关键：fwd_5d[i] = close[i+5] / close[i] - 1（如果 i+5 越界则为 NaN）
    """
    dates = pd.date_range(start="2024-01-01", periods=n_dates, freq="B")
    panel_rows = []
    for stock_idx in range(n_stocks):
        for i, date in enumerate(dates):
            close = 100.0 + i
            row = {
                "date": date,
                "code": f"60000{stock_idx}",
                "close": close,
                "fwd_5d": (close + 5) / close - 1 if i + 5 < n_dates else np.nan,
                "fwd_10d": (close + 10) / close - 1 if i + 10 < n_dates else np.nan,
                "fwd_20d": (close + 20) / close - 1 if i + 20 < n_dates else np.nan,
            }
            panel_rows.append(row)
    return pd.DataFrame(panel_rows)


@pytest.fixture
def pipeline_with_panel():
    """构造 MLDataPipeline 实例，panel 写入临时 parquet"""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "ml_panel.parquet"
        pipeline = MLDataPipeline(
            codes=["600000"], benchmark_code="000300", cache_path=str(cache_path)
        )
        yield pipeline


def test_train_split_drops_samples_with_label_in_val(pipeline_with_panel):
    """P1-3: 训练集最末 BUFFER 天的样本被剔除（其 fwd_5d 标签越界到 val）"""
    panel = _make_panel_with_forward_returns(n_dates=30)
    panel.to_parquet(pipeline_with_panel.cache_path, index=False)

    train, val, test = pipeline_with_panel.train_val_test_split(train_ratio=0.7, val_ratio=0.2)

    if len(train) == 0:
        pytest.skip("训练集为空（无法验证）")

    # 训练集最末日期 vs val 段起点
    train_max = train["date"].max()
    val_min = val["date"].min() if len(val) > 0 else None

    if val_min is not None:
        gap_days = (val_min - train_max).days
        # 修复后期望：训练集最末日期 < val 段起点至少 5 天（B 频率下 7 日历日）
        assert gap_days >= 5, \
            f"训练集最末日期 {train_max.date()} 应比 val 段起点 {val_min.date()} 早至少 5 天，实际 {gap_days} 天"

    # 训练集应有 16 个 B 频率日期（30 * 0.7 = 21，剔除最末 5 个剩 16）
    train_dates_unique = sorted(train["date"].unique())
    if len(train_dates_unique) > 5:
        assert len(train_dates_unique) == 16, \
            f"训练集应剩 16 个 B 频率日期，实际 {len(train_dates_unique)}"
    # 总样本数 = 16 日期 × 1 股票 = 16
    assert len(train) == 16, f"训练集应有 16 行，实际 {len(train)}"


def test_val_split_drops_samples_with_label_in_test(pipeline_with_panel):
    """P1-3: 验证集最末 BUFFER 天的样本被剔除（其 fwd_5d 标签越界到 test）"""
    panel = _make_panel_with_forward_returns(n_dates=60)
    panel.to_parquet(pipeline_with_panel.cache_path, index=False)

    train, val, test = pipeline_with_panel.train_val_test_split(train_ratio=0.5, val_ratio=0.2)

    if len(val) == 0 or len(test) == 0:
        pytest.skip("val 或 test 为空")

    val_max = val["date"].max()
    test_min = test["date"].min()
    gap_days = (test_min - val_max).days

    # 验证集最末日期应比 test 段起点早至少 5 天
    assert gap_days >= 5, \
        f"验证集最末日期 {val_max.date()} 应比 test 起点 {test_min.date()} 早至少 5 天，实际 {gap_days} 天"


def test_split_drops_boundary_samples(pipeline_with_panel):
    """P1-3: split 后总样本数应反映剔除（最末 N 天的样本被移除）"""
    panel = _make_panel_with_forward_returns(n_dates=100, n_stocks=2)
    panel.to_parquet(pipeline_with_panel.cache_path, index=False)

    train, val, test = pipeline_with_panel.train_val_test_split(train_ratio=0.6, val_ratio=0.2)

    total_split = len(train) + len(val) + len(test)
    total_panel = len(panel)

    # split 后样本数应 ≤ 原始 panel
    assert total_split <= total_panel, \
        f"split 后总样本 {total_split} 不应超过原始 {total_panel}"

    # 修复后期望：被剔除的样本数 = (train 末 5 天 + val 末 5 天) × 2 只股票 = 20
    excluded = total_panel - total_split
    assert excluded == 20, f"应剔除 20 个样本（train/val 各 5 天 × 2 只），实际 {excluded}"
