"""扫描任务状态机 —— 从 api.py 抽出的跨端点共享状态（RF-01 Phase 1）

背景：`/api/scan`（启动后台扫描）与 `/api/scan/status`（查询状态）**共享同一份
任务状态**。原先它定义在 api.py 的模块级，api.py 拆分后两端点会分属不同模块、
无法共享 ⇒ 属拆分硬阻塞。

本模块持有**唯一**状态对象；api.py 以 `from lib.scan_task_registry import _scan_tasks`
引用**同一个对象**（不是拷贝）⇒ 原地读写（`[preset] = ...` / `[preset]["status"] = ...`）
在两边都可见。

兼容：`from api import _scan_tasks` / `from api import ALLOWED_PRESETS` 仍然可用。
"""

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 允许的扫描档位
ALLOWED_PRESETS = {"balanced", "aggressive", "conservative"}

# preset -> {"status": "running"|"done"|"error", "started_at": float, "error": str}
_scan_tasks: Dict[str, dict] = {}


def start_task(preset: str) -> None:
    """登记一个扫描任务为 running（原地修改共享状态）。"""
    _scan_tasks[preset] = {"status": "running", "started_at": time.time(), "error": None}


def finish_task(preset: str) -> None:
    """标记任务完成（原地修改共享状态）。"""
    task = _scan_tasks.get(preset)
    if task is not None:
        task["status"] = "done"


def fail_task(preset: str, error: str) -> None:
    """标记任务失败（原地修改共享状态）。"""
    task = _scan_tasks.get(preset)
    if task is not None:
        task["status"] = "error"
        task["error"] = error


def get_task(preset: str) -> Optional[dict]:
    """取任务状态快照（只读）。"""
    return _scan_tasks.get(preset)
