"""跨进程单飞协调（批次 W 完整版）——零 Redis、零新依赖。

根因回顾：
    ``--workers N`` 下每个 worker 是独立进程，进程内锁（threading.Lock /
    asyncio.Task 注册表）互不可见。W 批轻量版用启动守卫拒绝多 worker；
    完整版改为**让多 worker 能正确工作**：把「只能做一次」的构造类工作
    交给跨进程协调，把「每个连接都要做」的推送留在各自进程。

设计：短文件锁竞争（不是长期选主）
    每个广播周期，各 worker 非阻塞抢一把 OS 文件锁：
      - 抢到的 worker：构造数据 → 写原子快照 → 释放锁；
      - 没抢到的 worker：直接读最近一次共享快照。
    文件锁由 OS 持有，**进程崩溃自动释放**，所以无需租约续期 / 心跳 /
    主节点失效检测——那些机制反而会引入新的竞态。

    快照用 ``tmp + os.replace`` 原子替换，读者要么看到旧的完整快照、
    要么看到新的完整快照，不会读到半写入，因此读侧不需要加锁。

单 worker 退化：
    ``multi_worker_mode()`` 为 False 时，所有协调函数都不产生任何文件 IO，
    调用方走原来的纯内存路径——单 worker 的行为、性能、L8 基线零变化。
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

# 路径基准（坑表 P-63）：模块在 <root>/lib/infra/ 下，parents[2] 即项目根。
# 禁止用 Path(__file__).parent 直接拼数据与日志路径。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = PROJECT_ROOT / "data" / "shared"

BROADCAST_SLOT_LOCK = "broadcast-slot.lock"
WS_SNAPSHOT = "ws-snapshot.json"
SNAPSHOT_MAX_AGE_S = 60  # 超过此年龄视为陈旧，调用方应回退自身构造

# 多 worker 同时首次访问共享目录时，Windows 上并发 mkdir / create 会抛
# PermissionError（实测：双 worker 启动报 [Errno 13]）。统一走带短重试的
# 底层 helper；不吞其他错误，避免掩盖真实权限问题。
_IO_RETRIES = 6
_IO_BACKOFF_S = 0.05


def _ensure_shared_dir() -> None:
    for attempt in range(_IO_RETRIES):
        try:
            SHARED_DIR.mkdir(parents=True, exist_ok=True)
            return
        except PermissionError:
            if attempt == _IO_RETRIES - 1:
                raise
            time.sleep(_IO_BACKOFF_S)


def _open_with_retry(path: Path, flags: int, mode: int = 0o644):
    for attempt in range(_IO_RETRIES):
        try:
            return os.open(str(path), flags, mode)
        except PermissionError:
            if attempt == _IO_RETRIES - 1:
                raise
            time.sleep(_IO_BACKOFF_S)
    raise AssertionError("unreachable")  # pragma: no cover


def multi_worker_mode() -> bool:
    """是否处于跨进程协调模式。

    判定与 worker_guard 一致：命令行 > 环境变量。单 worker 时为 False，
    保证协调代码完全不进入热路径。
    """
    from lib.infra.worker_guard import declared_worker_count
    return declared_worker_count() > 1


@contextmanager
def try_file_lock(name: str) -> Iterator[bool]:
    """非阻塞尝试获取跨进程独占文件锁，yield 是否抢到。

    跨平台：Windows 用 msvcrt.locking，POSIX 用 fcntl.flock。
    锁文件本身长期存在（不删除）——删除锁文件会让两个进程各锁一个 inode，
    这是经典的 file-lock 竞态。
    """
    _ensure_shared_dir()
    lock_path = SHARED_DIR / name
    fd = _open_with_retry(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    acquired = False
    try:
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def write_snapshot(name: str, payload: dict) -> None:
    """原子写共享快照；带写入时间戳供读者判断新鲜度。"""
    _ensure_shared_dir()
    target = SHARED_DIR / name
    # 每进程独立 tmp 文件名：避免两个 worker 同时写同一 tmp 导致 replace 竞争。
    tmp = SHARED_DIR / f"{name}.{os.getpid()}.tmp"
    body = json.dumps({"ts": time.time(), "data": payload},
                      ensure_ascii=False, default=str)
    for attempt in range(_IO_RETRIES):
        try:
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == _IO_RETRIES - 1:
                raise
            time.sleep(_IO_BACKOFF_S)


def read_snapshot(name: str, max_age: float = SNAPSHOT_MAX_AGE_S) -> Optional[dict]:
    """读共享快照；不存在 / 损坏 / 过期都返回 None（调用方自行回退构造）。"""
    try:
        raw = (SHARED_DIR / name).read_text(encoding="utf-8")
        record = json.loads(raw)
        if time.time() - float(record["ts"]) > max_age:
            return None
        return record["data"]
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError):
        return None


def try_claim_scan(key: str, lease_s: float = 120.0) -> bool:
    """跨进程扫描槽认领：成功返回 True 的 worker 才真正执行扫描。

    用「槽文件 + 租约」而非文件锁：扫描可能持续数十秒，长期持 OS 锁虽然
    崩溃自动释放，但其他 worker 无法读到进度、也无法判断僵死。槽文件写
    当前进程 PID 与时间戳，超时（lease_s）后任何 worker 都可强制接管。

    竞态安全：创建用 ``os.open(O_CREAT|O_EXCL)`` 原子操作；两个 worker
    同时抢时只有一个能创建成功。

    :param key: 扫描标识（如 ``dashboard:balanced``），会做文件名安全化。
    """
    _ensure_shared_dir()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    slot = SHARED_DIR / ("scan-" + safe + ".claim")
    now = time.time()
    try:
        fd = _open_with_retry(slot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # 已有人认领：检查租约是否过期，过期则强制接管（原子删除后下次可抢）。
        try:
            age = now - slot.stat().st_mtime
        except OSError:
            return False
        if age > lease_s:
            try:
                slot.unlink()
            except OSError:
                pass
        return False
    try:
        os.write(fd, f"{os.getpid()} {now}".encode("ascii"))
    finally:
        os.close(fd)
    return True


def release_scan(key: str) -> None:
    """释放扫描槽；只删自己的（按 PID 匹配），避免误删他人刚认领的槽。"""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    slot = SHARED_DIR / ("scan-" + safe + ".claim")
    try:
        content = slot.read_text(encoding="ascii").strip()
        if content.split(" ", 1)[0] == str(os.getpid()):
            slot.unlink()
    except (FileNotFoundError, OSError, ValueError):
        pass


def scan_claimed(key: str, lease_s: float = 120.0) -> bool:
    """其他 worker 是否在扫描中（租约内视为有效）。"""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    slot = SHARED_DIR / ("scan-" + safe + ".claim")
    try:
        return time.time() - slot.stat().st_mtime <= lease_s
    except OSError:
        return False


def wait_for_peer_scan(cache_reader, key: str, cache_key: str,
                       rounds: int = 10, interval_s: float = 0.5,
                       lease_s: float = 120.0):
    """等待其他 worker 的扫描结果落盘（同步，供端点 to_thread 调用）。

    把轮询的磁盘 IO 与 sleep 关在 lib/infra 内，端点只做一次
    ``asyncio.to_thread``——与 P-94 的 backup restore 同构：L8 记为 offloaded，
    不增加冻结端点的 blocking 计数。

    :return: (found, payload)；found=False 表示租约失效且未拿到结果。
    """
    for _ in range(rounds):
        cached = cache_reader(cache_key, max_age=60)
        if cached is not None:
            return True, cached
        if not scan_claimed(key, lease_s=lease_s):
            break
        time.sleep(interval_s)
    return False, None


def shared_ws_payload(compute) -> dict:
    """跨进程单飞构造 WS 数据：抢到锁的构造+写快照，否则读快照。

    :param compute: 无参可调用对象，返回待广播的 payload（dict）。
    :return: 所有 worker 内容一致的 payload。
    """
    # 等待模型（修 P-97 冷启动竞态的第二版）：
    #   没抢到锁的进程在等待期**反复重抢锁**，而不是固定等 N 毫秒后读快照。
    #   这样持有者一旦完成并释放锁，等待者下一次重抢就能拿到锁——
    #   拿到锁后先读快照：有则直接用（不重复构造），无则自己构造。
    #   这消除了「多个等待者同一时刻超时 → 一起 fallback compute」的竞态，
    #   也避免在 Windows spawn 慢启动时固定等待窗口不够的问题。
    for attempt in range(40):  # 40 × 0.05s = 2s
        with try_file_lock(BROADCAST_SLOT_LOCK) as got_lock:
            if got_lock:
                # 拿到锁：先看是否已有刚写入的快照；有就直接复用。
                cached = read_snapshot(WS_SNAPSHOT)
                if cached is not None:
                    return cached
                data = compute()
                write_snapshot(WS_SNAPSHOT, data)
                return data
        # 没拿到锁：短暂等待后重抢，不在这里 compute。
        time.sleep(0.05)

    # 2s 内始终拿不到锁（持有者可能卡死）：最后做一次无锁快照读取。
    cached = read_snapshot(WS_SNAPSHOT)
    if cached is not None:
        return cached
    # 仍无快照才本地构造——宁可多算一次，也不能让客户端收到空数据。
    return compute()
