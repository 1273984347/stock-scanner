"""P1-2: lib/strategies.py quick_score 命名误导修复测试

修复前: 内部变量名 tech_return_10d 实际是当日涨跌幅, 误导读者
修复后: 改名 pct_chg_proxy + docstring 明确"仅快速评分近似"
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import inspect


def test_p1_2_quick_score_uses_pct_chg_proxy_variable():
    """P1-2: quick_score 内部应使用 pct_chg_proxy 变量名"""
    from lib import strategies
    source = inspect.getsource(strategies.quick_score)
    assert "pct_chg_proxy" in source, (
        f"P1-2 修复未完成: quick_score 未使用 pct_chg_proxy 变量名。\n"
        f"源码:\n{source}"
    )


def test_p1_2_quick_score_docstring_warns_about_proxy():
    """P1-2: docstring 应明确 pct_chg 是'快速评分近似'"""
    from lib.strategies import quick_score
    doc = inspect.getdoc(quick_score) or ""
    assert "近似" in doc or "proxy" in doc.lower(), (
        f"P1-2 修复未完成: quick_score docstring 未注明 pct_chg 是近似值。\n"
        f"当前 docstring:\n{doc}"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
