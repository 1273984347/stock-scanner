"""RF-01 Phase 6 第 2 批 —— 共享状态加锁的机器判据。

原先 `routers/scan.py` 与 `routers/common.py` 直接读写 `_scan_tasks` / `_perf_metrics`，
并发下会读到半更新状态、丢指标或重复写文件。本文件验证「收口到唯一带锁 API」
之后这些失败模式不再发生；同时守住兼容契约（原容器对象仍是同一个）。
"""

from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from lib.infra import perf_metrics as pm
from lib.infra import scan_task_registry as registry


@pytest.fixture(autouse=True)
def _clean_state():
    registry.clear_tasks()
    pm.clear_metrics()
    yield
    registry.clear_tasks()
    pm.clear_metrics()


def test_scan_task_50_rounds_never_exposes_half_updated_state():
    """写线程反复制造 running/done/error，读线程持续取快照。"""
    observed = []
    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            snap = registry.get_task("balanced")
            if snap is None:
                continue
            if set(snap) != {"status", "started_at", "error"}:
                errors.append(f"incomplete keys: {snap}")
            if snap["status"] not in ("running", "done", "error"):
                errors.append(f"invalid status: {snap}")
            if snap["status"] == "error" and not snap["error"]:
                errors.append(f"error status without message: {snap}")
            observed.append(snap["status"])

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        for i in range(50):
            registry.start_task("balanced")
            if i % 3 == 0:
                registry.fail_task("balanced", f"boom-{i}")
            else:
                registry.finish_task("balanced")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2)

    assert not errors, errors[:5]
    assert registry.get_task("balanced")["status"] in ("done", "error")
    assert observed, "reader never observed a state -- gate has no discriminating power"


def test_get_task_returns_a_snapshot_not_the_live_dict():
    registry.start_task("balanced")
    snap = registry.get_task("balanced")
    snap["status"] = "tampered"
    assert registry.get_task("balanced")["status"] == "running"


def test_concurrent_record_metric_loses_nothing(tmp_path):
    """8 线程 × 20 条 = 160，达到 50 自动落盘，最后手动 flush 收尾。"""
    target = tmp_path / "perf.json"
    barrier = threading.Barrier(8)

    def worker(wid):
        barrier.wait()
        for j in range(20):
            pm.record_metric({"path": f"/api/t/{wid}/{j}", "duration_ms": 1.0,
                              "status": 200}, flush_path=target)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    pm.flush_metrics(target)
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert len(stored) == 160
    assert len({r["path"] for r in stored}) == 160, "duplicated metric rows"
    assert not pm._perf_metrics, "buffer must be drained after a successful flush"


def test_failed_flush_keeps_the_buffer_for_retry(tmp_path):
    """落盘失败时不清空 —— 与改造前行为一致，下次 flush 可重试。"""
    target = tmp_path / "perf.json"
    for i in range(3):
        pm.record_metric({"path": f"/api/t/{i}"}, flush_path=target)
    with patch("pathlib.Path.write_text", side_effect=OSError("disk busy")):
        assert pm.flush_metrics(target) == 0
    assert len(pm._perf_metrics) == 3
    assert pm.flush_metrics(target) == 3
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 3


def test_legacy_container_identity_is_preserved(monkeypatch):
    """`from api import _scan_tasks / _perf_metrics` 必须仍是同一对象。"""
    monkeypatch.setenv("SKIP_HS300_PREFETCH", "1")
    import api  # noqa: F401
    assert api._scan_tasks is registry._scan_tasks
    assert api._perf_metrics is pm._perf_metrics
    assert api.ALLOWED_PRESETS is registry.ALLOWED_PRESETS
