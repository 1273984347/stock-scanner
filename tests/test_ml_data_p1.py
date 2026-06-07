"""ML data 3 P1 修复测试

P1-1: pe_percentile 始终为 NaN，应从 FACTOR_COLS 中移除（避免污染特征矩阵）
P1-2: add_cross_sectional_features 定义完整但 build_panel 从未调用（截面排名特征缺失）
P1-3: feature_target_split 过滤 NaN 后 qid 可能产生零大小组（np.bincount 会破坏 XGBRanker）
"""
import os, sys, tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, '.')

from lib.ml_data import FACTOR_COLS, MLDataPipeline


# ============================================================
# P1-1: pe_percentile 应从 FACTOR_COLS 中移除
# ============================================================

def test_p1_1_pe_percentile_not_in_factor_cols():
    """P1-1: pe_percentile 始终为 NaN（无基本面数据源），不应出现在 FACTOR_COLS 中

    修复前：pe_percentile 在 FACTOR_COLS 中 → 进入 X，整列 NaN 污染训练
    修复后：从 FACTOR_COLS 中移除，X 不含该列
    """
    assert "pe_percentile" not in FACTOR_COLS, (
        "pe_percentile 始终为 NaN（无基本面数据源），不应出现在 FACTOR_COLS 中；"
        "否则会作为整列 NaN 进入特征矩阵 X，污染 ML 训练（XGBoost/LightGBM 仍会接受但相当于无信号噪声）"
    )


# ============================================================
# P1-2: add_cross_sectional_features 必须被 build_panel 调用
# ============================================================

def test_p1_2_build_panel_calls_cross_sectional_features(monkeypatch):
    """P1-2: build_panel() 必须调用 add_cross_sectional_features，生成 *_rank 列

    修复前：add_cross_sectional_features 定义完整但从未被调用
    修复后：build_panel 合并所有股票后、计算超额收益前调用一次，生成 <col>_rank 列
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "ml_panel.parquet"
        pipeline = MLDataPipeline(
            codes=["600000", "600001"],
            benchmark_code="000300",
            cache_path=str(cache_path),
        )

        # Stub _get_benchmark 返回空（绕过 akshare 调用）
        monkeypatch.setattr(
            pipeline, "_get_benchmark", lambda: pd.DataFrame()
        )

        # Stub _process_single_stock 返回包含所有 FACTOR_COLS 的小面板
        dates = pd.date_range("2024-01-01", periods=5, freq="B")

        def fake_process(code):
            rows = []
            for i, d in enumerate(dates):
                row = {
                    "date": d,
                    "code": code,
                    "close": 100.0 + i,
                    "volume": 10000.0,
                    "fwd_5d": 0.01,
                    "fwd_10d": 0.02,
                    "fwd_20d": 0.03,
                }
                # 给所有现有因子列填值（确保 rank 能计算）
                for col in FACTOR_COLS:
                    if col in ("benchmark_ret_5d", "benchmark_ret_20d"):
                        continue
                    # 让两只股票值不同，rank 才有意义
                    row[col] = float(i + (1 if code == "600000" else 2))
                rows.append(row)
            return pd.DataFrame(rows)

        monkeypatch.setattr(pipeline, "_process_single_stock", fake_process)

        panel = pipeline.build_panel(force_rebuild=True)

        # 期望：至少一个 <col>_rank 列存在（证明 add_cross_sectional_features 被调用）
        rank_cols = [c for c in panel.columns if c.endswith("_rank")]
        assert len(rank_cols) > 0, (
            f"build_panel 输出应包含 *_rank 列（来自 add_cross_sectional_features），"
            f"实际列: {list(panel.columns)}"
        )

        # 进一步验证：rank 值应在 [0, 1] 区间内
        for col in rank_cols:
            vals = panel[col].dropna()
            if len(vals) > 0:
                assert vals.min() >= 0.0 and vals.max() <= 1.0, (
                    f"{col} rank 值应在 [0, 1]，实际 [{vals.min()}, {vals.max()}]"
                )


# ============================================================
# P1-3: feature_target_split 过滤 NaN 后 qid 必须密集（无零大小组）
# ============================================================

def test_p1_3_feature_target_split_no_empty_groups():
    """P1-3: feature_target_split 在 y.notna() 过滤后，qid 不应有 gap

    修复前：date_to_group 在过滤前构建。若某日 ALL y NaN，过滤后 groups 数组中
            会出现 gap（如 [0,0,2,2]），np.bincount 产生 [2,0,2] → XGBRanker 崩溃
    修复后：过滤后重新映射 qid 为密集的 [0, 1, ...], 且 np.bincount 无 0
    """
    pipeline = MLDataPipeline.__new__(MLDataPipeline)  # 绕过 __init__

    # 构造 3 日 × 2 股票的小 df，其中 date d1 全部 NaN
    df = pd.DataFrame({
        "date": pd.to_datetime([
            "2024-01-01", "2024-01-01",  # d0: 2 valid
            "2024-01-02", "2024-01-02",  # d1: 2 NaN → 应整组被剔除
            "2024-01-03", "2024-01-03",  # d2: 2 valid
        ]),
        "code": ["600000", "600001", "600000", "600001", "600000", "600001"],
        "rsi_14": [50.0, 60.0, 55.0, 65.0, 52.0, 62.0],
        "momentum_5": [1.0, 2.0, 1.5, 2.5, 1.2, 2.2],
        "excess_5d": [0.01, 0.02, np.nan, np.nan, 0.03, 0.04],
    })

    X, y, groups = pipeline.feature_target_split(df, target_col="excess_5d")

    # 1) 过滤后应只剩 4 行（d0 + d2，d1 整组被滤）
    assert len(X) == 4, f"应剩 4 行（d0/d2 各 2），实际 {len(X)}"
    assert len(y) == 4
    assert groups is not None and len(groups) == 4

    # 2) groups 应为密集的 [0,0,1,1]（不应是 [0,0,2,2]）
    unique_qids = sorted(set(groups.tolist()))
    assert unique_qids == list(range(len(unique_qids))), (
        f"qid 应密集 [0, 1, 2, ...]，实际 {unique_qids}（含 gap，"
        f"会导致 np.bincount 产生零大小组，破坏 XGBRanker）"
    )

    # 3) np.bincount(groups) 不应有 0（关键 invariant）
    sizes = np.bincount(groups)
    assert (sizes > 0).all(), (
        f"np.bincount(groups) 不应含 0，实际 {sizes.tolist()}"
        f"（零大小组会让 XGBRanker.set_group 报错或行为不可预测）"
    )

    # 4) sizes 应为 [2, 2]（d0 和 d2 各 2 只股票）
    assert sizes.tolist() == [2, 2], f"组大小应为 [2,2]，实际 {sizes.tolist()}"


def test_p1_3_feature_target_split_partial_nan_keeps_group():
    """P1-3 边界：某组只有部分样本 NaN，该组应保留（剔除 NaN 行，但组本身不删除）"""
    pipeline = MLDataPipeline.__new__(MLDataPipeline)

    df = pd.DataFrame({
        "date": pd.to_datetime([
            "2024-01-01", "2024-01-01",  # d0: 1 valid + 1 NaN → 保留组，1 行
            "2024-01-02", "2024-01-02",  # d1: 2 valid → 保留组，2 行
        ]),
        "code": ["600000", "600001", "600000", "600001"],
        "rsi_14": [50.0, 60.0, 55.0, 65.0],
        "excess_5d": [0.01, np.nan, 0.02, 0.03],
    })

    X, y, groups = pipeline.feature_target_split(df, target_col="excess_5d")

    # 剩 3 行
    assert len(X) == 3
    # groups 密集 [0, 1, 1]
    assert sorted(set(groups.tolist())) == [0, 1]
    sizes = np.bincount(groups)
    assert sizes.tolist() == [1, 2], f"组大小应为 [1, 2]，实际 {sizes.tolist()}"
