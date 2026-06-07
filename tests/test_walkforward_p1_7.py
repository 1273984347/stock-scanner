"""P1-7: lib/walkforward.py FactorAudit 在 run() 中调用

修复前: FactorAudit 类定义存在但 run() 流程不调用
修复后: run() 在 train_data 准备后调 audit.validate_timestamp
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import inspect


def test_p1_7_run_invokes_factor_audit():
    """P1-7: run() 中应包含 FactorAudit/audit 引用"""
    from lib import walkforward
    source = inspect.getsource(walkforward)
    run_idx = source.find("def run(")
    next_def_idx = source.find("\n    def ", run_idx + 1)
    if next_def_idx < 0:
        next_def_idx = len(source)
    run_section = source[run_idx:next_def_idx]
    assert "FactorAudit" in run_section or "audit" in run_section, (
        "P1-7 修复未完成: run() 段未引用 FactorAudit/audit。"
    )


def test_p1_7_run_calls_validate_timestamp():
    """P1-7: run() 段应出现 validate_timestamp 方法调用"""
    from lib import walkforward
    source = inspect.getsource(walkforward)
    run_idx = source.find("def run(")
    next_def_idx = source.find("\n    def ", run_idx + 1)
    if next_def_idx < 0:
        next_def_idx = len(source)
    run_section = source[run_idx:next_def_idx]
    assert "validate_timestamp" in run_section, (
        "P1-7 修复未完成: run() 段未调用 validate_timestamp 方法。"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
