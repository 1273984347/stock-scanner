"""批次 W 完整版：真实多 worker 集成判据。

这些判据不 mock 文件锁——用真实进程验证：
  1. 广播构造跨进程单飞：8 个进程同时调用 shared_ws_payload，构造次数 == 1；
  2. 所有进程拿到同一份数据（共享快照一致）；
  3. 扫描槽跨进程唯一：8 进程抢同一 key，只有一个认领成功。
"""

import multiprocessing
import os
import time

from lib.infra import process_leader as pl


def _broadcast_worker(shared_dir, out_queue, compute_count_path) -> None:
    pl.SHARED_DIR = shared_dir
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        with open(compute_count_path, "a", encoding="ascii") as f:
            f.write("c\n")
            f.flush()
        time.sleep(0.3)  # 模拟构造耗时，让其他进程有机会并发
        return {"worker": os.getpid(), "stamp": time.time()}

    result = pl.shared_ws_payload(compute)
    out_queue.put((os.getpid(), result, calls["n"]))
    with open(compute_count_path, "a", encoding="ascii") as f:
        f.write(f"local={calls['n']}\n")


def test_broadcast_construct_is_cross_process_singleton(tmp_path):
    """8 进程并发广播：只有一个进程构造，其余读同一快照。"""
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    count_path = tmp_path / "compute-count.txt"
    procs = [ctx.Process(target=_broadcast_worker,
                         args=(tmp_path, queue, count_path))
             for _ in range(8)]
    for p in procs:
        p.start()
    results = [queue.get(timeout=15) for _ in procs]
    for p in procs:
        p.join(timeout=10)
    assert all(p.exitcode == 0 for p in procs)

    # 构造次数（'c' 行）必须恰好 1——这是单飞的核心判据。
    raw = count_path.read_text(encoding="ascii")
    computes = [line for line in raw.splitlines() if line == "c"]
    assert len(computes) == 1, f"expected one compute, got {raw!r}"

    # 所有进程拿到的 payload 必须内容一致（来自同一快照）。
    payloads = {repr(r[1]) for r in results}
    assert len(payloads) == 1, f"workers saw different payloads: {payloads}"
    # 只有构造者本地计数为 1，其余为 0。
    assert sum(1 for r in results if r[2] == 1) == 1


def _scan_claim_worker(shared_dir, out_queue) -> None:
    pl.SHARED_DIR = shared_dir
    got = pl.try_claim_scan("dashboard:balanced", lease_s=120)
    out_queue.put((os.getpid(), got))
    time.sleep(0.5)


def test_scan_claim_is_cross_process_singleton(tmp_path):
    """8 进程抢同一扫描 key：只有一个拿到。"""
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    procs = [ctx.Process(target=_scan_claim_worker, args=(tmp_path, queue))
             for _ in range(8)]
    for p in procs:
        p.start()
    results = [queue.get(timeout=15) for _ in procs]
    for p in procs:
        p.join(timeout=10)
    assert sum(1 for _, got in results if got) == 1
