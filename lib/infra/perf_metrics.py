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

# RLock 只保护缓冲 list 本身（append / 快照 / 裁剪），不再包住文件 IO。
_perf_metrics_lock = threading.RLock()
# 独立锁串行化「读旧文件 → 合并 → 写盘」，防止两次 flush 各读到同一份旧文件。
_flush_io_lock = threading.Lock()

FLUSH_THRESHOLD = 50
MAX_STORED = 1000


def record_metric(entry: dict, flush_path: Path) -> bool:
    """带锁追加一条指标；达到阈值时在**锁外**落盘（批次 B3 / P2-5）。

    Returns:
        是否触发了 flush。
    """
    with _perf_metrics_lock:
        _perf_metrics.append(entry)
        should_flush = len(_perf_metrics) >= FLUSH_THRESHOLD
    # 必须出锁后再 flush：否则文件 IO 仍会占住缓冲锁，与改造前无异。
    if should_flush:
        flush_metrics(flush_path)
        return True
    return False


def flush_metrics(flush_path: Path) -> int:
    """把缓冲区落盘；文件 IO 移出缓冲锁，同时保持「不丢不重」。

    批次 B3 / P2-5：旧实现把「读旧文件 → json.loads → 拼 1000 条 →
    write_text」全放在 `_perf_metrics_lock` 内，一次落盘会串行化所有
    并发 `record_metric`（中间件每请求都调），还在事件循环里同步读写。

    新流程与并发正确性：
    * 在缓冲锁内**立即摘走**快照（而不是等 IO 成功才删），
      这样并发的第二次 flush 看到空缓冲就直接返回，不会重复快照同一批指标；
    * 文件 IO 在独立的 `_flush_io_lock` 下串行执行，缓冲锁此时已释放，
      `record_metric` 不受阻塞；
    * IO 失败把快照**还回缓冲头部**（保持顺序），下次重试，不丢指标；
    * IO 期间新 append 的条目留在尾部，不会被吞掉。

    Returns:
        本次成功落盘的条数。
    """
    with _perf_metrics_lock:
        if not _perf_metrics:
            return 0
        snapshot = list(_perf_metrics)
        del _perf_metrics[:len(snapshot)]

    try:
        with _flush_io_lock:
            existing = []
            if flush_path.exists():
                existing = json.loads(flush_path.read_text(encoding="utf-8"))
            all_metrics = (existing + snapshot)[-MAX_STORED:]
            flush_path.parent.mkdir(parents=True, exist_ok=True)
            flush_path.write_text(
                json.dumps(all_metrics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception as e:
        logger.debug(f"性能指标写入失败: {e}")
        with _perf_metrics_lock:
            _perf_metrics[:0] = snapshot  # 失败还回头部，保持原有顺序
        return 0
    return len(snapshot)


def clear_metrics() -> None:
    """仅供测试与运维重置，不用于业务路径。"""
    with _perf_metrics_lock:
        _perf_metrics.clear()
