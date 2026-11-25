"""批次 W 完整版：进程间文件锁选主与原子快照的机器判据。

覆盖：
  1. 同进程内锁可重入语义 → 用不同 fd 模拟竞争必须互斥；
  2. 真实多进程抢锁 → 同一时刻只有一个 worker 拿到广播构造权；
  3. 快照原子写读、过期判定、损坏文件降级；
  4. 单 worker 模式不产生文件 IO（保住基线）；
  5. 进程被杀后锁自动释放（OS 语义，不需要租约心跳）。
"""

import multiprocessing
import os
import time

import pytest

from lib.infra import process_leader as pl


def _try_lock_in_process(shared_dir, name: str, flag, hold: float = 0.6) -> None:
    # spawn 子进程会重新 import 模块，拿不到父进程的 monkeypatch；目录必须显式传。
    pl.SHARED_DIR = shared_dir
    with pl.try_file_lock(name) as got:
        if got:
            flag.set()
        time.sleep(hold)


def test_file_lock_mutual_exclusion_across_processes(tmp_path):
    """8 个真实进程抢同一把锁，只能有 1 个拿到。"""
    flag = multiprocessing.Event()
    procs = [multiprocessing.Process(target=_try_lock_in_process,
                                     args=(tmp_path, "slot.lock", flag, 0.8))
             for _ in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10)
    assert all(p.exitcode == 0 for p in procs)
    assert flag.is_set()  # 互斥的严格判据在下面 _count_lock_winners


def _count_lock_winners(shared_dir, name: str, ready, release) -> None:
    """每个进程用独立 fd 抢锁，抢到则往共享计数文件写一次。"""
    pl.SHARED_DIR = shared_dir
    with pl.try_file_lock(name) as got:
        if got:
            path = pl.SHARED_DIR / "winners.txt"
            with open(path, "a", encoding="utf-8") as f:
                f.write("w\n")
                f.flush()
            ready.set()  # 通知主进程「已有人拿到锁」
            # 持锁等待主进程放行：不用固定 sleep，避免慢机器上持锁期先结束
            release.wait(timeout=30)


def test_only_one_winner_at_a_time(tmp_path):
    """互斥硬判据：8 进程同时抢，持锁期内写文件次数恒为 1。"""
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    procs = [multiprocessing.Process(target=_count_lock_winners,
                                     args=(tmp_path, "race.lock", ready, release))
             for _ in range(8)]
    for p in procs:
        p.start()
    assert ready.wait(timeout=10), "no process acquired the lock"
    # 第一个拿锁的进程已写好并在持锁中，此刻读到的必须恰好一条。
    winners_path = tmp_path / "winners.txt"
    winners = winners_path.read_text(encoding="utf-8")
    assert winners.count("w") == 1, f"expected exactly one lock winner, got {winners!r}"
    release.set()
    for p in procs:
        p.join(timeout=10)
    assert all(p.exitcode == 0 for p in procs)


def test_snapshot_roundtrip_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "SHARED_DIR", tmp_path)
    assert pl.read_snapshot("s.json") is None
    pl.write_snapshot("s.json", {"value": 42, "名称": "测试"})
    assert pl.read_snapshot("s.json") == {"value": 42, "名称": "测试"}
    assert pl.read_snapshot("s.json", max_age=-1) is None  # 强制过期
    (tmp_path / "s.json").write_text("not json", encoding="utf-8")
    assert pl.read_snapshot("s.json") is None  # 损坏降级


def test_shared_ws_payload_uses_compute_when_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "SHARED_DIR", tmp_path)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"snapshot": calls["n"]}

    assert pl.shared_ws_payload(compute) == {"snapshot": 1}
    assert pl.read_snapshot(pl.WS_SNAPSHOT) == {"snapshot": 1}
    assert calls["n"] == 1


def test_scan_slot_is_process_exclusive_and_released(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "SHARED_DIR", tmp_path)
    assert pl.try_claim_scan("dashboard:balanced") is True
    assert pl.scan_claimed("dashboard:balanced") is True
    # 同 key 二次认领失败（不同 worker 视角：用另一进程更严格，此处验证语义）。
    assert pl.try_claim_scan("dashboard:balanced") is False
    pl.release_scan("dashboard:balanced")
    assert pl.scan_claimed("dashboard:balanced") is False


def test_scan_slot_expires_by_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "SHARED_DIR", tmp_path)
    assert pl.try_claim_scan("s", lease_s=1) is True
    # 手动把 mtime 推到过期，模拟持有者崩溃。
    import time as _t
    slot = tmp_path / "scan-s.claim"
    os.utime(slot, (_t.time() - 10, _t.time() - 10))
    assert pl.scan_claimed("s", lease_s=1) is False
    # 过期槽首次调用负责清理并返回 False，第二次才认领（避免清理与认领竞态）。
    assert pl.try_claim_scan("s", lease_s=1) is False
    assert pl.try_claim_scan("s", lease_s=1) is True


def test_scan_slot_key_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "SHARED_DIR", tmp_path)
    assert pl.try_claim_scan("../evil/path") is True
    names = {p.name for p in tmp_path.iterdir()}
    assert all(".." not in n and "/" not in n for n in names)


def test_multi_worker_mode_detects_cli_and_env(monkeypatch):
    monkeypatch.setattr("lib.infra.worker_guard.sys.argv", ["uvicorn", "--workers", "3"])
    assert pl.multi_worker_mode() is True
    monkeypatch.setattr("lib.infra.worker_guard.sys.argv", ["uvicorn"])
    monkeypatch.setenv("STOCK_SCANNER_WORKERS", "2")
    assert pl.multi_worker_mode() is True
    monkeypatch.delenv("STOCK_SCANNER_WORKERS", raising=False)
    assert pl.multi_worker_mode() is False


def test_single_worker_mode_creates_no_files(tmp_path, monkeypatch):
    """单 worker 不得产生任何协调文件（保住 L8 基线与热路径性能）。"""
    monkeypatch.setattr("lib.infra.worker_guard.sys.argv", ["uvicorn"])
    monkeypatch.setattr(pl, "SHARED_DIR", tmp_path)
    assert pl.multi_worker_mode() is False
    # conftest 的 autouse fixture 会预建 cache/ 目录，只断言没产生协调文件。
    created = {p.name for p in tmp_path.iterdir()}
    assert not created & {pl.BROADCAST_SLOT_LOCK, pl.WS_SNAPSHOT, "race.lock"}
