"""P0-5: lib/ml_trainer.py 网格搜索改用 TimeSeriesSplit + embargo

修复前：
- _grid_search_xgb / _grid_search_lgbm 用 nfold=3（随机 K-Fold）
- 对 forward-return 标签来说，未来日期必然泄漏到训练折
- "最佳超参"基于泄漏 NDCG 选优，IC 几乎确定被高估

修复后：
- 自定义 _TimeSeriesSplitWithEmbargo
- 训练折严格在验证折之前
- 训练折末尾留 5 天 embargo（避免 fwd_Nd 标签跨段）
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from lib.ml.ml_trainer import _TimeSeriesSplitWithEmbargo


def _make_panel(n_dates=120, n_features=5):
    """构造简单的时序数据：n_dates 行 × n_features 列"""
    X = np.random.RandomState(42).randn(n_dates, n_features)
    y = np.random.RandomState(43).randint(0, 10, size=n_dates)
    return X, y


def test_timeseriessplit_basic_ordering():
    """P0-5: 所有 train_idx 元素必须 < 所有 val_idx 元素（严格时序）"""
    X, _ = _make_panel(100)
    splitter = _TimeSeriesSplitWithEmbargo(n_splits=3, embargo_gap=5)

    for train_idx, val_idx in splitter.split(X):
        # train 中最大的索引必须 < val 中最小的索引
        assert train_idx.max() < val_idx.min(), \
            f"train max {train_idx.max()} >= val min {val_idx.min()} (违反时序)"
        # val 必须非空
        assert len(val_idx) > 0
        # train 必须非空（除非数据极少，但 100 行 n_splits=3 至少 1 个 train）
        assert len(train_idx) > 0


def test_embargo_gap_applied():
    """P0-5: embargo 间隔应让 train 末尾腾出 5 天"""
    X, _ = _make_panel(100)
    from sklearn.model_selection import TimeSeriesSplit
    plain = TimeSeriesSplit(n_splits=3)
    embarged = _TimeSeriesSplitWithEmbargo(n_splits=3, embargo_gap=5)

    # 对比第一折的 train 长度
    for (t1, _), (t2, _) in zip(plain.split(X), embarged.split(X)):
        # 带 embargo 的 train 应比 plain 短 5
        assert len(t2) == len(t1) - 5, \
            f"embargo 长度不对：plain={len(t1)} embargoed={len(t2)}"


def test_splits_are_not_random():
    """P0-5: 验证不是随机 K-Fold——同一数据多次 split 应得到完全相同的索引"""
    X, _ = _make_panel(100)
    splitter1 = _TimeSeriesSplitWithEmbargo(n_splits=3, embargo_gap=5)
    splitter2 = _TimeSeriesSplitWithEmbargo(n_splits=3, embargo_gap=5)

    splits1 = list(splitter1.split(X))
    splits2 = list(splitter2.split(X))

    # 每次调用应得到相同的 (train, val) 对
    for (t1, v1), (t2, v2) in zip(splits1, splits2):
        assert np.array_equal(t1, t2)
        assert np.array_equal(v1, v2)


def test_n_splits_count():
    """P0-5: n_splits 控制 fold 数量"""
    X, _ = _make_panel(100)
    for n_splits in [2, 3, 4]:
        splitter = _TimeSeriesSplitWithEmbargo(n_splits=n_splits, embargo_gap=5)
        splits = list(splitter.split(X))
        assert len(splits) == n_splits


def test_xgb_lgb_uses_timeseriessplit_not_kfold():
    """P0-5: 验证 _grid_search_xgb 和 _grid_search_lgbm 内部使用 TimeSeriesSplit 而非 nfold

    通过直接检查 ml_trainer.py 源码（防止回退到 nfold=3）
    """
    import inspect
    import lib.ml.ml_trainer as ml_trainer
    src = inspect.getsource(ml_trainer)
    # 找所有 cv() 调用（xgb.cv 和 lgb.cv）
    # 实际参数应该是 folds=... 不应该是 nfold=3
    import re
    cv_calls = re.findall(r'(xgb|lgb)\.cv\([^)]*\)', src, re.DOTALL)
    bad_calls = []
    for call in cv_calls:
        # nfold= 出现就报
        if re.search(r'\bnfold\s*=', call):
            bad_calls.append(call[:100])
    assert len(bad_calls) == 0, \
        f"ml_trainer.py 中仍有 cv() 调用使用 nfold=: {bad_calls}"


def test_xgb_lgb_cv_uses_folds_timeseriessplit():
    """P0-5: xgb.cv / lgb.cv 调用应使用 folds=TimeSeriesSplit-like 参数"""
    import re
    import lib.ml.ml_trainer as ml_trainer
    # 按行检查：去掉注释行（包括 docstring 中的）
    src = open(ml_trainer.__file__, encoding='utf-8').read()
    in_docstring = False
    code_lines = []
    for line in src.split("\n"):
        stripped = line.strip()
        # 跟踪 docstring 状态
        if '"""' in stripped or "'''" in stripped:
            in_docstring = not in_docstring if stripped.count('"""') == 1 or stripped.count("'''") == 1 else False
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        # 去掉行内注释（# 后是注释）
        if "#" in line:
            line_no_comment = line[:line.index("#")]
        else:
            line_no_comment = line
        if line_no_comment.strip():
            code_lines.append(line_no_comment)
    code_only = "\n".join(code_lines)

    # 现在搜代码（去除注释/docstring）
    nfold_in_arg = re.findall(r'\bnfold\s*=\s*\d', code_only)
    folds_in_arg = re.findall(r'\bfolds\s*=', code_only)
    timeseries_count = len(re.findall(r'TimeSeriesSplit', code_only))

    assert len(nfold_in_arg) == 0, f"代码中仍使用 nfold= 参数: {nfold_in_arg}"
    assert len(folds_in_arg) >= 2, f"应该有 ≥2 个 folds= 调用，实际 {len(folds_in_arg)}"
    assert timeseries_count >= 2, f"TimeSeriesSplit 应至少出现 2 次（class + 使用），实际 {timeseries_count}"


def test_embargo_with_small_data_does_not_crash():
    """P0-5: 数据极少时（n_dates 接近 n_splits+embargo）应优雅处理，不崩溃"""
    X, _ = _make_panel(15)  # 极少数据
    splitter = _TimeSeriesSplitWithEmbargo(n_splits=3, embargo_gap=5)
    splits = list(splitter.split(X))
    # 不要求每个 fold 都有 train（可能为空），但 val 应该有
    for _, val_idx in splits:
        assert len(val_idx) > 0
