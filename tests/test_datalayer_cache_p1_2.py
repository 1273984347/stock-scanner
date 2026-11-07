"""P1-2: dim-2 P1-2 修复 - lib/data_layer.py 缓存读失败不删文件

修复前：line 97-102 except Exception → 删文件（不可区分 IO 错误与文件损坏）
- 一旦文件被其他进程占用/磁盘满/权限错误，缓存会被静默删除
- 下次 cache miss → 重新拉取 → 实盘性能下降

修复后：区分错误类型
- 文件损坏（OSError/pyarrow 解码失败）→ 删文件（合理行为）
- IO 错误（PermissionError / IsADirectoryError）→ 不删，warning（保留文件待人工诊断）
- 其他错误 → 不删，warning
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


def test_load_cache_io_error_does_not_delete_file():
    """P1-2: IO 错误（PermissionError）不应删文件（保留待人工诊断）"""
    import lib.data.data_layer as data_layer
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "good.parquet"
        # 先创建一个有效文件
        df_good = pd.DataFrame({"a": [1, 2, 3]})
        df_good.to_parquet(cache_path, index=False)

        # mock pd.read parquet 抛 PermissionError（IO 错误）
        with patch.object(data_layer, "_cache_path", return_value=cache_path):
            with patch.object(pd, "read_parquet", side_effect=PermissionError("文件被占用")):
                df = data_layer._load_cache("test_key", max_age_hours=2)

        # 文件应**保留**（不删除），让运维诊断权限问题
        assert cache_path.exists(), "PermissionError 时文件应保留，不应被静默删除"
        assert df.empty, f"PermissionError 应返回空 df（视为 cache miss）"


def test_load_cache_other_errors_do_not_delete_file():
    """P1-2: 未知错误（如 KeyError）不应删文件"""
    import lib.data.data_layer as data_layer
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "good.parquet"
        df_good = pd.DataFrame({"a": [1, 2, 3]})
        df_good.to_parquet(cache_path, index=False)

        with patch.object(data_layer, "_cache_path", return_value=cache_path):
            with patch.object(pd, "read_parquet", side_effect=KeyError("字段缺失")):
                df = data_layer._load_cache("test_key", max_age_hours=2)

        # KeyError 也保留文件（不可区分损坏 vs 应用 bug）
        assert cache_path.exists(), "KeyError 时文件应保留"
        assert df.empty


def test_load_cache_source_code_handles_io_errors_safely():
    """P1-2: 验证修复后 source code 区分了错误类型"""
    import inspect
    import lib.data.data_layer as data_layer
    source = inspect.getsource(data_layer)

    # 找 _load_cache 段
    load_cache_idx = source.find("def _load_cache")
    if load_cache_idx < 0:
        pytest.skip("data_layer.py 不含 _load_cache（可能已重构）")

    # _load_cache 段到下一个 def 之前
    next_def_idx = source.find("\ndef ", load_cache_idx + 1)
    if next_def_idx < 0:
        next_def_idx = len(source)
    load_cache_section = source[load_cache_idx:next_def_idx]

    # 必须有 PermissionError 单独处理
    assert "except PermissionError" in load_cache_section, \
        "_load_cache 必须单独处理 PermissionError（不删文件）"
    assert "except IsADirectoryError" in load_cache_section, \
        "_load_cache 必须单独处理 IsADirectoryError（不删文件）"

    # 不应再有 'except Exception: path.unlink' 模式
    assert "except Exception:\n        path.unlink" not in source, \
        "data_layer.py 仍含 'except Exception: path.unlink' 静默删文件"
