"""性能指标缓冲 —— 跨端点共享状态（RF-01 Phase 1 抽出 / Phase 6 加锁并收口）。

Phase 1：中间件在每个 `/api/*` 请求后累积指标，满 50 条落盘；拆分 `api.py`
后需要一个跨模块可见的唯一 list，故由本模块持有。

Phase 6（计划 §三 Phase 6 第 4 点）：原先 `routers/common.py` 直接
`_perf_metrics.append(...)`，并在 `_flush_metrics()` 里做
「读旧文件 → 拼接 → 截断 → 写文件 → `.clear()`」这串非原子操作。
并发请求下会发生两类问题：append 与 clear 交错导致指标丢失；
两次 flush 同时读到同一份旧文件导致重复写入。

根因修法：把读写**全部收口到本模块唯一带锁 API**，业务侧只调用
`record_metric` / `flush_metrics`，不再直接碰底层容器。
`_perf_metrics` 仍保持原 list 对象（`from api import _perf_metrics` 兼容契约成立），
但新代码禁止直接读写。

兼容：`from api import _perf_metrics` 仍然可用。
"""

import json
import logging
import threading
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# 性能指标缓冲（仅由下列带锁 API 原地 append / 删除）
_perf_metrics: List[dict] = []

# RLock：record_metric 达阈值时会在锁内调用 flush_metrics（重入）。
_perf_metrics_lock = threading.RLock()

FLUSH_THRESHOLD = 50
MAX_STORED = 1000


def record_metric(entry: dict, flush_path: Path) -> bool:
    """带锁追加一条指标；达到阈值时在**同一把锁内**落盘。

    Returns:
        是否触发了 flush。
    """
    with _perf_metrics_lock:
        _perf_metrics.append(entry)
        if len(_perf_metrics) >= FLUSH_THRESHOLD:
            flush_metrics(flush_path)
            return True
    return False


def flush_metrics(flush_path: Path) -> int:
    """把缓冲区落盘并清除，整段原子。

    文件 IO 留在锁内是刻意选择：Phase 6 优先保证「不丢不重」，
    且每 50 条才触发一次，串行化代价可忽略。异常时**不清空**缓冲区，
    与改造前行为一致（下次 flush 重试）。

    Returns:
        本次成功落盘的条数。
    """
    with _perf_metrics_lock:
        if not _perf_metrics:
            return 0
        snapshot = list(_perf_metrics)
        try:
            existing = []
            if flush_path.exists():
                existing = json.loads(flush_path.read_text(encoding="utf-8"))
            all_metrics = (existing + snapshot)[-MAX_STORED:]
            flush_path.parent.mkdir(parents=True, exist_ok=True)
            flush_path.write_text(
                json.dumps(all_metrics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 只删已落盘的前缀，不 .clear()：若将来把 IO 移出锁，
            # flush 期间新 append 的条目也不会被吞掉。
            del _perf_metrics[:len(snapshot)]
            return len(snapshot)
        except Exception as e:
            logger.debug(f"性能指标写入失败: {e}")
            return 0


def clear_metrics() -> None:
    """仅供测试与运维重置，不用于业务路径。"""
    with _perf_metrics_lock:
        _perf_metrics.clear()
