"""C12 零星收尾判据（2026-11-20）：half_open 探测名额单飞（P2-6）/ hist 缓存命中
轻校验（P2-4）/ 追加日志保留期清理（P1-3 零星项）。

patch 口径（I4）：host_health 用 fixture 提供的独立实例（不碰进程级单例，
conftest autouse reset 亦兼容）；data_layer 缓存读写经 data_layer._save_cache
（conftest 隔离覆盖 CACHE_DIR）；get_stock_hist 的多源取数 patch 使用方
``data_layer.get_stock_hist_multi_source``（顶层绑定）。
"""

import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from lib.data import data_layer, host_health as hh
from scripts import cleanup_logs


# ---------------------------------------------------------------- half_open 名额单飞


@pytest.fixture()
def health():
    """独立实例 + 可推进的假时钟（不触进程级单例；conftest reset 兼容）。"""
    class _Clock:
        now = 1000.0

        def __call__(self):
            return self.now

        def advance(self, sec):
            self.now += sec

    clock = _Clock()
    h = hh.HostHealth(clock=clock)
    return h, clock


def test_half_open_grants_single_probe_slot(health):
    """冷却到期 → 第一个调用者拿到探测名额；并发第二个调用者被跳过（P2-6 核心）。"""
    h, c = health
    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    c.advance(301)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True   # 名额授予
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False  # 名额已被占用
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False


def test_probe_slot_released_on_success_and_reopen(health):
    """探测成功 → 名额释放且 closed；探测失败 → 立即重开并释放名额。"""
    h, c = health
    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    c.advance(301)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True
    h.note_success(hh.HOST_EASTMONEY_PUSH2)
    assert hh.HOST_EASTMONEY_PUSH2 not in h._probe_slots
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True  # closed 直通

    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    c.advance(301)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True
    h.note_failure(hh.HOST_EASTMONEY_PUSH2, RuntimeError("still down"))
    assert hh.HOST_EASTMONEY_PUSH2 not in h._probe_slots  # 重开释放
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False


def test_stale_probe_slot_released_after_timeout(health):
    """挂死探测：名额持有超过 PROBE_SLOT_TIMEOUT_S → 有界释放，重新放行探测。"""
    h, c = health
    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    c.advance(301)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True   # 占名额
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False
    c.advance(hh.PROBE_SLOT_TIMEOUT_S + 1)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True   # 超时释放，重新探测
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False  # 新名额又被占用


def test_probe_slots_not_in_snapshot_and_reset(health):
    """名额簿独立于 _states：snapshot 响应形状零变化；reset 一并清零（P-100）。"""
    h, c = health
    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    c.advance(301)
    h.is_available(hh.HOST_EASTMONEY_PUSH2)
    snap = h.snapshot()[hh.HOST_EASTMONEY_PUSH2]
    assert "probe" not in json.dumps(snap)
    assert set(snap) == {"state", "failures", "opened_at", "last_error", "last_change"}
    h.reset()
    assert h._probe_slots == {}


import json  # noqa: E402 —— 上方测试用到，置于文件尾部 import 区之外保持可读


# ---------------------------------------------------------------- hist 缓存命中校验


def _valid_df(days: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    return pd.DataFrame({
        "date": idx, "open": 10.0, "high": 11.0, "low": 9.0,
        "close": 10.5, "volume": 1000.0,
    })




def _double_data_layer_dict():
    """P-118 回归守卫：get_stock_hist 的 globals 必须仍是 data_layer 自身字典
    （backtest/full.py 的模块级全局猴补丁已于 C13 删除——若复发此断言判红）。"""
    import lib.data.data_layer as _dl
    return _dl.get_stock_hist.__globals__ is not _dl.__dict__


def test_hist_cache_hit_valid_frame_no_refetch(monkeypatch):
    """合规缓存帧 → 直接返回，多源取数零调用（不因校验引入重取）。"""
    assert not _double_data_layer_dict(), "P-118 回归：模块级全局猴补丁复发"
    data_layer._save_cache("hist/C12VALID/30", _valid_df())
    import os as _os
    calls = []

    def _poison(*a, **k):
        calls.append(1)
        return _valid_df()

    # P-118 候选：全量下存在两个 data_layer 实例（get_stock_hist.__globals__ 不是
    # data_layer.__dict__，实测 ab_testing 之后必现）——patch 必须落在其真实
    # globals（setitem），对实例分身免疫
    monkeypatch.setitem(data_layer.get_stock_hist.__globals__,
                        "get_stock_hist_multi_source", _poison)
    df = data_layer.get_stock_hist("C12VALID", days=30)
    assert len(df) == 30 and calls == []


def test_hist_cache_invalid_frame_triggers_refetch(monkeypatch):
    """P2-4 核心：缺 close 列的坏帧 → 视为 miss 重取（重取成功覆盖写盘）。"""
    assert not _double_data_layer_dict(), "P-118 回归：模块级全局猴补丁复发"
    bad = _valid_df().drop(columns=["close"])
    data_layer._save_cache("hist/C12BAD/30", bad)
    monkeypatch.setitem(data_layer.get_stock_hist.__globals__,
                        "get_stock_hist_multi_source", lambda *a, **k: _valid_df())
    df = data_layer.get_stock_hist("C12BAD", days=30)
    assert "close" in df.columns and len(df) == 30
    # 覆盖写盘后缓存自愈
    fixed = data_layer._load_cache("hist/C12BAD/30", max_age_hours=24)
    assert "close" in fixed.columns


# ---------------------------------------------------------------- 日志保留期清理


def test_cleanup_logs_removes_expired_only(tmp_path):
    old = tmp_path / "logs" / "paper_daily_2026-10-01.log"
    new = tmp_path / "logs" / "paper_daily_2026-11-20.log"
    sched_old = tmp_path / "logs" / "scheduled" / "tdx_update.log"
    keep_jsonl = tmp_path / "logs" / "scheduled" / "keep.jsonl"
    for f in (old, new, sched_old, keep_jsonl):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    # 31 天前
    stale = time.time() - 31 * 86400
    os_utime = __import__("os").utime
    os_utime(old, (stale, stale))
    os_utime(sched_old, (stale, stale))

    removed = cleanup_logs.cleanup(tmp_path, retention_days=30)

    assert str(old.relative_to(tmp_path)) in removed
    assert not old.exists()
    assert new.exists(), "30 天内文件不得误删"
    assert sched_old.exists() is False
    assert keep_jsonl.exists(), "非 .log 模式不得误删"


def test_cleanup_logs_thread_safety_is_trivially_true(tmp_path):
    """并发清理同一批文件：unlink 幂等（OSError 吞掉），总量守恒不抛错。"""
    target = tmp_path / "logs" / "paper_daily_old.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    stale = time.time() - 31 * 86400
    __import__("os").utime(target, (stale, stale))

    errs = []

    def worker():
        try:
            cleanup_logs.cleanup(tmp_path, retention_days=30)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errs == [] and not target.exists()


def test_cleanup_logs_prunes_expired_backup_zips(tmp_path):
    """Δ7（2026-11-22，增量评审）：backups/*.zip 按 30 天保留——API 备份端点
    产物此前无限累积。"""
    old = tmp_path / "backups" / "backup_2026-10-01.zip"
    new = tmp_path / "backups" / "backup_2026-11-20.zip"
    for f in (old, new):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    stale = time.time() - 31 * 86400
    __import__("os").utime(old, (stale, stale))

    removed = cleanup_logs.cleanup(tmp_path, retention_days=30)

    assert str(old.relative_to(tmp_path)) in removed
    assert not old.exists() and new.exists(), "30 天内的 zip 不得误删"
