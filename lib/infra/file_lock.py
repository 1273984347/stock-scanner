"""跨进程文件锁（增量评审 Δ4/N1，2026-11-22）。

背景（docs/audit/architecture-review-delta-2026-11-22.md Δ4）：local_store 的
manifest 是整文件 read-modify-write，此前只有进程内线程锁——API 服务与
``scripts/data_sync.py`` 两个进程并发写同一批 key 时，manifest 跨进程
last-writer-wins 丢更新（账实漂移）、data_sync 可双实例同跑互踩进度。

两把语义不同的锁，**都不要**用 ``process_leader``（那是排他 leader 选主语义，
worker_guard 单 worker 前提下 API 进程恒为 leader，data_sync 永远拿不到
租约——评审复核 D③ 否决了复用方案）：

* :func:`cross_process_lock` —— 写边界互斥：任何进程都可获取，持有期间
  其它进程的获取阻塞（Windows ``msvcrt.locking`` / POSIX ``fcntl.flock``）。
  临界区必须短（manifest RMW），不得在持锁期间做网络/长 IO；
* :func:`acquire_session_lock` / :func:`try_session_lock` —— 会话单飞：
  非阻塞尝试，被占用（含同进程第二个句柄）立即失败。

坑位纪律：
* Windows 重试用 ``msvcrt.LK_LOCK``（OS 级 1s 间隔重试，约 10s 上限），
  **不经 Python ``time.sleep``**——坑表 P-81：conftest 全局把 time.sleep
  patch 成 no-op，sleep 重试环在测试里会变成热自旋；
* 锁文件不进备份/对账面：调用方放 ``cache/`` 或 ``data/store/`` 根（后者是
  文件、``_disk_truth`` 只扫目录，不会被当成数据集）。
"""
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover — 项目目标平台为 Windows，POSIX 分支为可移植兜底
    import fcntl


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _try_lock(fd: int) -> None:
    """非阻塞试锁；被占用抛 OSError。"""
    if os.name == "nt":
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _lock_blocking(fd: int) -> None:
    """阻塞拿锁。Windows LK_LOCK = OS 级 1s 重试（约 10s 上限）；POSIX 无超时。"""
    if os.name == "nt":
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def cross_process_lock(path):
    """写边界互斥锁：任何进程都可获取，持有期间**其它进程**阻塞（约 ≤10s）。

    Windows 语义（2026-11-22 判据实测）：跨进程 = LK_LOCK 的 OS 级 1s 间隔
    重试；**同进程第二句柄 = 立即抛 OSError(EDEADLOCK)**——同进程并发由
    调用方线程锁串行化（local_store 中跨进程锁恒在线程锁外层），不得嵌套。

    用法（local_store manifest RMW 收口，Δ4）::

        with cross_process_lock(_manifest_lock_path()):
            with _LOCK:          # 线程锁在内层，顺序全仓一致防死锁
                ... read-modify-write ...
    """
    path = Path(path)
    _ensure_parent(path)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    try:
        _lock_blocking(fd)
        yield
    finally:
        try:
            _unlock(fd)
        except OSError:
            pass  # 解锁失败不影响语义：进程退出即释放 OS 锁
        finally:
            os.close(fd)


def acquire_session_lock(path) -> Optional[int]:
    """非阻塞获取会话单飞锁；成功返回 fd（进程退出前须 release，或依赖
    进程退出自动释放），被占用返回 None。"""
    path = Path(path)
    _ensure_parent(path)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    try:
        _try_lock(fd)
        return fd
    except OSError:
        os.close(fd)
        return None


def release_session_lock(fd: int) -> None:
    try:
        _unlock(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextmanager
def try_session_lock(path):
    """:func:`acquire_session_lock` 的上下文管理器形态：yield True=已持有，
    False=被占用（不阻塞、不持有）。"""
    fd = acquire_session_lock(path)
    try:
        yield fd is not None
    finally:
        if fd is not None:
            release_session_lock(fd)
