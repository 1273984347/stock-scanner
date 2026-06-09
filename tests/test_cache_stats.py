"""get_cache_stats 单元测试 (2026-06-09 cherry-pick from wip/pipeline-refactor)

覆盖:
- 初始状态全 0
- load_cache 各分支 (miss / expired / hit / error) 正确累加
- get_cache_stats 返回 dict 副本（外部修改不影响内部）
- reset_cache_stats 全清零
- thread-safe 并发场景 (smoke test)
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from lib import cache as cache_mod


@pytest.fixture(autouse=True)
def reset_stats():
    """每个测试前后重置 stats，避免污染"""
    cache_mod.reset_cache_stats()
    yield
    cache_mod.reset_cache_stats()


def test_get_cache_stats_initial_all_zero():
    """初始状态: 4 个 counter 全 0"""
    stats = cache_mod.get_cache_stats()
    assert stats == {"hits": 0, "misses": 0, "expired": 0, "errors": 0}


def test_load_cache_miss_increments_misses():
    """load_cache 文件不存在 → misses += 1"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(cache_mod, "CACHE_DIR", Path(tmpdir)):
            cache_mod.load_cache("nonexistent")
            stats = cache_mod.get_cache_stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 0
    assert stats["expired"] == 0
    assert stats["errors"] == 0


def test_load_cache_hit_increments_hits():
    """load_cache 文件存在且未过期 → hits += 1"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache_file = tmp / "valid.parquet"
        pd.DataFrame({"a": [1, 2, 3]}).to_parquet(cache_file, index=False)
        with patch.object(cache_mod, "CACHE_DIR", tmp):
            result = cache_mod.load_cache("valid")
            stats = cache_mod.get_cache_stats()
    assert len(result) == 3, "P2-5 fix 保留: hit 返非空 DataFrame"
    assert stats["hits"] == 1
    assert stats["misses"] == 0


def test_load_cache_expired_increments_expired():
    """load_cache 文件过期 → expired += 1"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache_file = tmp / "stale.parquet"
        pd.DataFrame({"a": [1]}).to_parquet(cache_file, index=False)
        # 设置 mtime 为 3 小时前
        import os, time
        old_time = time.time() - 3 * 3600
        os.utime(cache_file, (old_time, old_time))
        with patch.object(cache_mod, "CACHE_DIR", tmp):
            cache_mod.load_cache("stale", max_age_hours=2)
            stats = cache_mod.get_cache_stats()
    assert stats["expired"] == 1
    assert stats["misses"] == 0
    assert stats["hits"] == 0


def test_load_cache_error_increments_errors_and_returns_none():
    """load_cache 文件损坏 → errors += 1, 返 None (P2-5 fix 保留)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        cache_file = tmp / "corrupt.parquet"
        cache_file.write_text("not a parquet file")
        with patch.object(cache_mod, "CACHE_DIR", tmp):
            result = cache_mod.load_cache("corrupt")
            stats = cache_mod.get_cache_stats()
        # 注意: assert 必须在 with 块内, tmpdir 退出时会清理
        assert result is None, "P2-5 fix 保留: 错误返 None (非空 df)"
        assert stats["errors"] == 1
        # P2-5 fix 保留: 失败时保留文件
        assert cache_file.exists(), "P2-5 fix 保留: 失败时文件不删"


def test_get_cache_stats_returns_copy():
    """get_cache_stats 返回内部 dict 的 copy, 外部修改不影响内部"""
    stats = cache_mod.get_cache_stats()
    stats["hits"] = 999
    # 重新获取应不受影响
    fresh = cache_mod.get_cache_stats()
    assert fresh["hits"] == 0, "外部修改 copy 不应影响内部 _cache_stats"


def test_reset_cache_stats_clears_all():
    """reset_cache_stats 全清零"""
    # 故意污染
    cache_mod._cache_stats["hits"] = 100
    cache_mod._cache_stats["misses"] = 50
    cache_mod.reset_cache_stats()
    stats = cache_mod.get_cache_stats()
    assert stats == {"hits": 0, "misses": 0, "expired": 0, "errors": 0}


def test_concurrent_load_cache_thread_safe():
    """并发 load_cache 不应丢失 counter 更新 (smoke test)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # 创建 10 个 valid cache files
        for i in range(10):
            (tmp / f"k{i}.parquet").write_text("stub")  # 故意坏文件触发 errors
        with patch.object(cache_mod, "CACHE_DIR", tmp):
            threads = []
            for i in range(10):
                t = threading.Thread(target=cache_mod.load_cache, args=(f"k{i}",))
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
            stats = cache_mod.get_cache_stats()
    # 10 个并发 error 应全被记录
    assert stats["errors"] == 10, f"并发累加应等于 10, 实际 {stats['errors']}"
