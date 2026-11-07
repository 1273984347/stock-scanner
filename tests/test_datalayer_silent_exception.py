"""P1-1: dim-2 P1-1 修复 - lib/data_layer.py 静默吞异常

修复前：lib/data_layer.py:520 except Exception: continue 静默吞所有 pytdx 错误
- akshare/pytdx 限流、网络错误、数据缺失都被吞掉
- 实盘遇到数据问题时无法诊断

修复后：打 warning log（不静默），并累计错误后报告
"""
import os, sys
sys.path.insert(0, '.')

import logging
import pytest
from unittest.mock import patch, MagicMock


def test_pytdx_connection_failure_logs_warning():
    """P1-1: pytdx 全部服务器连接失败时，必须打 warning log（不静默）

    验证修复后 source code 包含 logger.warning（不靠 caplog 捕获）
    """
    import inspect
    import lib.data.data_layer as data_layer
    source = inspect.getsource(data_layer)

    # 找 pytdx 连接代码段
    pytdx_idx = source.find("TdxHq_API")
    if pytdx_idx < 0:
        pytest.skip("data_layer.py 不含 pytdx 连接代码（可能已重构）")

    # pytdx 段前 80 行（包含 except 块）
    pytdx_section = source[pytdx_idx:pytdx_idx + 5000]

    # 必须有 logger.warning 在 except 块附近
    assert "logger.warning" in pytdx_section, \
        "pytdx 连接段必须含 logger.warning（不静默）"

    # 不能再有 'except Exception: continue' 静默模式
    assert "except Exception:\n                continue" not in source, \
        "data_layer.py 仍含 'except Exception: continue' 静默吞异常"


def test_pytdx_exception_is_not_silently_swallowed():
    """P1-1: 验证修复后 - pytdx 异常不会被 'except Exception: continue' 静默吞掉"""
    # 直接读 lib/data_layer.py:520 附近代码
    import inspect
    import lib.data.data_layer as data_layer
    source = inspect.getsource(data_layer)

    # 不应有 "except Exception: continue" 这种模式
    # 注：原始代码是 "except Exception: continue"（silent fallback）
    # 修复后期望：要么去掉 continue，要么加上 logger.warning
    suspicious_patterns = [
        "except Exception:\n                continue",
        "except Exception: continue",
        "except: continue",
    ]
    for pattern in suspicious_patterns:
        assert pattern not in source, \
            f"data_layer.py 仍含静默吞异常的 '{pattern}'——P1-1 修复未完成"


def test_pytdx_exception_caught_with_logger():
    """P1-1: pytdx 异常被捕获时伴随 logger.warning"""
    import inspect
    import lib.data.data_layer as data_layer
    source = inspect.getsource(data_layer)

    # 在 pytdx 连接相关代码段中，必须有 logger.warning 或 logger.debug
    if "TdxHq_API" in source:
        # 检查 pytdx 段有 warning
        pytdx_section = source[source.find("TdxHq_API"):]
        # 找 pytdx 段前 50 行
        pytdx_lines = pytdx_section[:3000]

        assert "logger.warning" in pytdx_lines or "logger.error" in pytdx_lines, \
            "pytdx 段必须含 logger.warning/error 记录异常（不静默）"
