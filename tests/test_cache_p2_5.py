"""P2-5: lib/cache.py load_cache 失败升级 error + 显式 None 标记

修复前（lib/cache.py 旧版本）：
- parquet 读取失败 → logger.debug + 删文件 + 返回 pd.DataFrame()
- 上游无法区分"缓存未命中"（空 df）和"加载失败"（也空 df）

修复后（2026-06-07 P2-5）：
- 缓存未命中（文件不存在 / 过期）→ pd.DataFrame()（空，预期行为）
- 加载失败（损坏 / PermissionError / IO 错误）→ logger.error + **不删文件** + 返回 None
- 上游用 `is None` 显式判断加载失败（与 api.py 现有调用模式一致）
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


def _make_cache_dir(monkeypatch, tmp_path):
    """把 CACHE_DIR 重定向到临时目录（不污染项目 cache/）。"""
    import lib.data.cache as cache_mod
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)
    return tmp_path


def test_load_cache_file_not_exists_returns_empty_no_error(caplog):
    """P2-5: 文件不存在 → 返回空 df，**不**打 error 日志（cache miss 是预期）"""
    import lib.data.cache as cache_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # 不创建任何文件 → cache miss
        with patch.object(cache_mod, "CACHE_DIR", tmp_path):
            with _capture_lib_logs(cache_mod, caplog, logging.DEBUG):
                result = cache_mod.load_cache("missing_key", max_age_hours=2)

    assert result is not None, "cache miss 应返回 DataFrame（非 None）"
    assert isinstance(result, pd.DataFrame), f"应返回 DataFrame，实际 {type(result)}"
    assert result.empty, "cache miss 应返回空 DataFrame"
    # 不能有 ERROR 级别日志
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == "lib.data.cache"]
    assert not error_records, f"cache miss 不应打 error 日志，实际有: {[r.getMessage() for r in error_records]}"


def test_load_cache_file_expired_returns_empty_and_deletes():
    """P2-5: 文件过期 → 删 + 返回空 df，文件不存在"""
    import lib.data.cache as cache_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        cache_file = tmp_path / "expired_key.parquet"  # 必须匹配 _cache_path("expired_key")
        df = pd.DataFrame({"a": [1, 2, 3]})
        df.to_parquet(cache_file, index=False)

        # 把 mtime 改成 3 小时前（max_age_hours=2 会过期）
        old_time = (datetime.now() - timedelta(hours=3)).timestamp()
        os.utime(cache_file, (old_time, old_time))

        with patch.object(cache_mod, "CACHE_DIR", tmp_path):
            result = cache_mod.load_cache("expired_key", max_age_hours=2)

        assert isinstance(result, pd.DataFrame)
        assert result.empty, "过期缓存应返回空 DataFrame"
        assert not cache_file.exists(), "过期文件应被删除"


def test_load_cache_corrupt_returns_none_and_logs_error(caplog):
    """P2-5 核心：parquet 损坏 → logger.error + 返回 None（显式错误标记）"""
    import lib.data.cache as cache_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        cache_file = tmp_path / "corrupt_key.parquet"  # 必须匹配 _cache_path("corrupt_key")
        # 写入**无效** parquet 数据（损坏）
        cache_file.write_bytes(b"NOT_A_PARQUET_FILE_GARBAGE_DATA")

        with patch.object(cache_mod, "CACHE_DIR", tmp_path):
            with _capture_lib_logs(cache_mod, caplog, logging.ERROR):
                result = cache_mod.load_cache("corrupt_key", max_age_hours=2)

    # 1. 必须返回 None（显式错误标记）
    assert result is None, f"加载失败应返回 None，实际返回 {type(result)}: {result!r}"
    # 2. 必须打 ERROR 日志（不再静默）
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == "lib.data.cache"]
    assert error_records, f"加载失败应打 error 日志，实际没有。全部记录: {[(r.name, r.levelname) for r in caplog.records]}"
    # 3. 日志内容应包含 key 和错误类型
    error_msg = error_records[0].getMessage()
    assert "corrupt_key" in error_msg, f"错误日志应包含 key，实际: {error_msg}"
    assert "[cache]" in error_msg, f"错误日志应带 [cache] 前缀"


def test_load_cache_io_error_returns_none_and_preserves_file(caplog):
    """P2-5: IO 错误（PermissionError）→ None + **不删文件**（保留待人工诊断）

    与 P1-2 (data_layer) 行为一致：PermissionError 保留文件。
    """
    import lib.data.cache as cache_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        cache_file = tmp_path / "io_error_key.parquet"  # 必须匹配 _cache_path("io_error_key")
        # 写一个看起来有效的文件
        df = pd.DataFrame({"a": [1, 2, 3]})
        df.to_parquet(cache_file, index=False)

        # mock pd.read_parquet 抛 PermissionError
        with patch.object(cache_mod, "CACHE_DIR", tmp_path):
            with patch.object(pd, "read_parquet", side_effect=PermissionError("文件被占用")):
                with _capture_lib_logs(cache_mod, caplog, logging.ERROR):
                    result = cache_mod.load_cache("io_error_key", max_age_hours=2)

        # 1. 返回 None
        assert result is None, f"IO 错误应返回 None，实际 {type(result)}"
        # 2. 文件**保留**（核心修复点 — 不能静默删）
        assert cache_file.exists(), "IO 错误时应保留文件，不能静默删除"
        # 3. 打 error 日志
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == "lib.data.cache"]
        assert error_records, f"IO 错误应打 error 日志。全部: {[(r.name, r.levelname) for r in caplog.records]}"


def test_load_cache_valid_returns_dataframe(caplog):
    """P2-5: 正常加载 → 返回 DataFrame，无 error 日志"""
    import lib.data.cache as cache_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        cache_file = tmp_path / "valid_key.parquet"  # 必须匹配 _cache_path("valid_key")
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        df.to_parquet(cache_file, index=False)

        with patch.object(cache_mod, "CACHE_DIR", tmp_path):
            with _capture_lib_logs(cache_mod, caplog, logging.DEBUG):
                result = cache_mod.load_cache("valid_key", max_age_hours=2)

    assert isinstance(result, pd.DataFrame)
    assert not result.empty
    assert len(result) == 3
    assert list(result.columns) == ["a", "b"]
    # 无 error 日志（只看 lib.data.cache）
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == "lib.data.cache"]
    assert not error_records, f"正常加载不应打 error 日志，实际: {[r.getMessage() for r in error_records]}"


def test_load_cache_source_code_returns_optional_dataframe():
    """P2-5: 源码签名必须明确返回 Optional[DataFrame]（None 是合法返回值）"""
    import inspect
    import lib.data.cache as cache_mod
    sig = inspect.signature(cache_mod.load_cache)
    return_annotation = sig.return_annotation
    # 接受 Union[DataFrame, None] / Optional[DataFrame] / DataFrame | None
    ann_str = str(return_annotation)
    assert "None" in ann_str or "Optional" in ann_str, \
        f"load_cache 返回类型应明确为 Optional[DataFrame]，实际: {ann_str}"


def test_load_cache_source_code_logs_error_on_failure():
    """P2-5: 源码必须 logger.error 升级（不能是 logger.debug / logger.warning）"""
    import inspect
    import lib.data.cache as cache_mod
    source = inspect.getsource(cache_mod.load_cache)
    # 错误处理块必须有 logger.error
    assert "logger.error" in source, \
        "load_cache 必须调用 logger.error（不能继续用 logger.debug）"
    # 不能还在用 logger.debug 记录加载失败
    assert "logger.debug(f\"[cache] 缓存读取失败" not in source, \
        "load_cache 不应再用 logger.debug 记录加载失败（已升级到 error）"


# ============================================================================
# Helpers
# ============================================================================

from contextlib import contextmanager

@contextmanager
def _capture_lib_logs(cache_mod, caplog, level):
    """在测试期间让 lib.data.cache 日志能进 caplog。

    背景：tests/logging.conf 把 `lib` logger 设为 propagate=0，
    pytest caplog 默认挂在 root logger 上，所以拿不到 lib.data.cache 的记录。
    这里直接给 lib.data.cache 挂一个把记录写进 caplog.records 的 handler（最可靠）。
    """
    class _CaplogHandler(logging.Handler):
        def emit(self, record):
            caplog.records.append(record)
            if record.levelno >= logging.WARNING or caplog.handler.level <= logging.DEBUG:
                caplog.handler.handle(record)

    saved_level = cache_mod.logger.level
    h = _CaplogHandler(level=level)
    cache_mod.logger.addHandler(h)
    cache_mod.logger.setLevel(level)
    try:
        yield
    finally:
        cache_mod.logger.removeHandler(h)
        cache_mod.logger.setLevel(saved_level)

