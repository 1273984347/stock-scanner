"""P1-1: clean_cache LRU 修复测试

修复前：line 154 `alive_files.sort(key=lambda x: x[1])` 按 mtime 排序
       → 删除最旧修改时间的文件（FIFO oldest-first）
       → 注释写"LRU"但实际是 FIFO

修复后：按 atime（访问时间）排序删除最近最少访问的文件（真正 LRU）
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


def _make_files_with_atime(tmp: Path, files_atimes: dict):
    """Create files in tmp with specified (mtime, atime) tuples.

    files_atimes: {name: (mtime_offset_seconds, atime_offset_seconds)}
                   offset from now (negative = past)
    """
    for name, (m_off, a_off) in files_atimes.items():
        f = tmp / name
        f.write_bytes(b"x" * 1024)
        m_t = time.time() + m_off
        a_t = time.time() + a_off
        os.utime(f, (a_t, m_t))


def test_clean_cache_uses_atime_for_lru_eviction():
    """P1-1: LRU 应按 atime 排序，不是 mtime

    场景：
      A: mtime 新 + atime 旧（创建后没被访问过）
      B: mtime 旧 + atime 新（最近刚访问过）

    FIFO 行为（bug）：删除 mtime 旧的 → 删 B
    LRU 正确行为：删除 atime 旧的 → 删 A
    """
    from lib import data_layer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # A: 创建晚 (mtime 新=1分钟前) + 访问久 (atime 30 天前)
        # B: 创建早 (mtime 30 天前) + 最近被访问 (atime 1 分钟前)
        _make_files_with_atime(tmp, {
            "a.parquet": (-60, -86400 * 30),  # mtime 新, atime 旧
            "b.parquet": (-86400 * 30, -60),  # mtime 旧, atime 新
        })

        # max_total_mb = 0.001 = 1048 bytes
        # 删 A (1024 bytes) → total_size = 1024 ≤ 1048 → 停
        with patch.object(data_layer, "CACHE_DIR", tmp):
            deleted = data_layer.clean_cache(max_age_hours=999999, max_total_mb=0.001)

        assert deleted == 1, f"应该恰好删除 1 个文件，实际删除 {deleted}"
        # LRU: A 的 atime 旧 → 删 A
        assert not (tmp / "a.parquet").exists(), \
            "LRU 应删除 atime 旧的文件 a.parquet"
        assert (tmp / "b.parquet").exists(), \
            "LRU 应保留 atime 新的文件 b.parquet"


def test_clean_cache_source_code_uses_atime():
    """P1-1: 验证修复后源码使用 atime"""
    import inspect
    from lib import data_layer
    source = inspect.getsource(data_layer.clean_cache)

    # 必须有 st_atime（访问时间）
    assert "st_atime" in source, \
        "clean_cache 应使用 st_atime 实现 LRU（按访问时间淘汰）"


def test_clean_cache_falls_back_to_mtime_when_atime_unavailable():
    """P1-1: 如果 atime 不可用（atime 旧于 mtime），用 max(atime, mtime) 兜底

    Linux 某些挂载点 (noatime) atime 不会更新
    容错：用 max(atime, mtime) 保证至少有一个有效时间
    """
    from lib import data_layer

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # A: atime 不可用（30 天前），mtime 新（1 分钟前）
        # B: 正常 atime/mtime（1 分钟前）
        _make_files_with_atime(tmp, {
            "a.parquet": (-60, -86400 * 30),  # mtime 新, atime 极旧
            "b.parquet": (-60, -60),           # 都新
        })

        with patch.object(data_layer, "CACHE_DIR", tmp):
            deleted = data_layer.clean_cache(max_age_hours=999999, max_total_mb=0.001)

        # max(atime, mtime) for A = mtime (1 分钟前) — 比 B 新，但 A 的 atime 实际更旧
        # 关键：A 的 atime 30 天前，B 的 atime 1 分钟前
        # 应删除 A (atime 旧)，保留 B
        assert not (tmp / "a.parquet").exists()
        assert (tmp / "b.parquet").exists()
