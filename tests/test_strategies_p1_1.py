"""P1-1: lib/strategies.py use_judge_panel 死代码修复测试

修复前: 'config' in dir() 永远 False, getattr(config, ...) 永不被求值
修复后: 删除 JudgePanel 死代码块
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import inspect
import ast
import pandas as pd
import numpy as np


def test_p1_1_no_bare_config_name_outside_config_param():
    """P1-1: 函数体内裸 Name 'config' 引用必须仅在以 config 为参数的函数中"""
    from lib import strategies
    source = inspect.getsource(strategies)
    tree = ast.parse(source)

    bad_refs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arg_names = {a.arg for a in node.args.args}
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id == "config":
                    if "config" not in arg_names:
                        bad_refs.append((sub.lineno, node.name))

    assert len(bad_refs) == 0, (
        f"P1-1 修复未完成: lib/strategies.py 存在未导入的 'config' Name 引用:\n"
        + "\n".join(f"  L{ln} in def {fn}()" for ln, fn in bad_refs)
        + "\n（修复: 删除 JudgePanel 死代码块）"
    )


def test_p1_1_full_score_runs_without_name_error():
    """P1-1: 修复后 full_score 不再因裸 config 引用崩溃"""
    from lib.strategies import full_score
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(60) * 0.5)
    ohlcv = pd.DataFrame({
        "date": dates,
        "open": close, "high": close, "low": close, "close": close,
        "volume": [1e6] * 60,
    })
    row = pd.Series({
        "代码": "000001", "市盈率-动态": 8.5,
        "量比": 1.2, "换手率": 1.0, "成交额": 5e8,
        "涨跌幅": 1.5, "60日涨跌幅": 5.0,
    })
    factors = {"volume_ratio": 1.2, "momentum_5": 2.0, "momentum_20": 5.0}
    result = full_score("000001", ohlcv, factors, row)
    assert isinstance(result, dict)
    assert "score" in result


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
