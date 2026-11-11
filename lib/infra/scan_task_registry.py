"""扫描任务状态机 —— 跨端点共享状态（RF-01 Phase 1 抽出 / Phase 6 加锁）。

背景：`/api/scan`（启动后台扫描）与 `/api/scan/status`（查询状态）共享同一份任务状态。
Phase 5 拆分后两端点分属不同模块，故由本模块持有唯一状态对象。

Phase 6 加锁（RULES §1.7 / 计划 §三 Phase 6 第 4 点）：
后台线程写 `status` / `error`，端点读 `status` / `started_at` / `error`，
原先是**散落的直接字典读写**（routers/scan.py 与 routers/common.py 各自碰 `_scan_tasks`），
并发下会读到半更新状态，且「失败分支写两个字段」不是原子操作。

根因修法不是给每个调用处补 `with lock`（那仍会漏、且锁散落），
而是**把读写全部收口到本模块唯一带锁 API**：
  - `start_task` / `finish_task` / `fail_task` / `update_task`：写，锁内原子完成；
  - `get_task`：读，返回**浅拷贝快照**，调用方不会拿到正在被后台线程修改的原 dict；
  - `_scan_tasks` 容器对象本身保持不变（`from api import _scan_tasks` 兼容契约仍成立），
    但**新代码禁止直接读写它**，一律走上述 API。
"""

import logging
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 允许的扫描档位
ALLOWED_PRESETS = {"balanced", "aggressive", "conservative"}

# preset -> {"status": "running"|"done"|"error", "started_at": float, "error": str}
_scan_tasks: Dict[str, dict] = {}

# RLock：record/flush 之类的嵌套调用可重入；不引入模块级可变状态之外的东西。
_scan_tasks_lock = threading.RLock()


def start_task(preset: str) -> None:
    """登记一个扫描任务为 running（原子替换整条记录）。"""
    with _scan_tasks_lock:
        _scan_tasks[preset] = {
            "status": "running", "started_at": time.time(), "error": None
        }


def finish_task(preset: str) -> None:
    """标记任务完成。"""
    with _scan_tasks_lock:
        task = _scan_tasks.get(preset)
        if task is not None:
            task["status"] = "done"


def fail_task(preset: str, error: str) -> None:
    """标记任务失败 —— status 与 error 在同一次锁内写完，杜绝半更新状态。"""
    with _scan_tasks_lock:
        task = _scan_tasks.get(preset)
        if task is not None:
            task["status"] = "error"
            task["error"] = error


def update_task(preset: str, **fields) -> None:
    """一次锁内原子更新多个字段（供后台线程扩展使用）。"""
    with _scan_tasks_lock:
        task = _scan_tasks.get(preset)
        if task is not None:
            task.update(fields)


def get_task(preset: str) -> Optional[dict]:
    """取任务状态**快照**（浅拷贝），避免读到并发写了一半的 dict。"""
    with _scan_tasks_lock:
        task = _scan_tasks.get(preset)
        return dict(task) if task is not None else None


def clear_tasks() -> None:
    """仅供测试与运维重置，不用于业务路径。"""
    with _scan_tasks_lock:
        _scan_tasks.clear()
