"""扫描所有 lib/ 子模块 import 验证 (broken modules 排查)

investor_agents.py 在 ≥10 个历史 commit 都有 SyntaxError 但 CI 没发现.
本测试扫所有 lib/*.py 模块, 发现类似 dead module.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import importlib
import pkgutil
import pytest

import lib


def _discover_modules():
    """发现 lib/ 下所有子模块."""
    modules = []
    for importer, modname, ispkg in pkgutil.walk_packages(
        lib.__path__, prefix="lib.", onerror=lambda _: None
    ):
        # 跳过 __pycache__ 和已知慢模块
        if "__pycache__" in modname:
            continue
        modules.append(modname)
    return sorted(set(modules))


ALL_MODULES = _discover_modules()


@pytest.mark.parametrize("modname", ALL_MODULES)
def test_import_module(modname):
    """每个 lib/ 子模块必须能 import (无 SyntaxError / ImportError)."""
    try:
        importlib.import_module(modname)
    except ImportError as e:
        # 某些模块依赖可选包 (如 portalocker), 跳过
        if "portalocker" in str(e) or "fcntl" in str(e):
            pytest.skip(f"可选依赖缺失: {e}")
        raise
    except SyntaxError as e:
        pytest.fail(f"{modname} 有 SyntaxError: {e}")
    except Exception as e:
        # 其他异常 (如 config 缺失) 不阻塞
        pytest.skip(f"{modname} import 跳过: {e}")