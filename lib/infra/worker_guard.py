"""多 worker 部署守卫（批次 W）。

根因（为什么不是「重复推送」）：
    应用的全部可变状态都是**进程内内存**——
    ``routers.common.manager.active_connections``、
    ``lib.infra.scan_task_registry._scan_tasks`` / ``_inflight_scan``、
    ``lib.infra.api_core._cache_*``。``uvicorn --workers N`` 会 fork N 个
    独立进程，每份状态互不可见，于是：

      1. 每个 worker 各跑一份 ``_ws_broadcast_loop``，数据源被拉 N 倍；
      2. B4 的「同 preset 单飞」锁只在进程内有效，多 worker 下形同虚设，
         同一 preset 可被 N 个 worker 并发扫描、缓存互相覆盖；
      3. 客户端落到不同 worker 会读到不同快照，数据不一致。

    **单客户端不会收到重复推送**——一条 TCP 连接只落在一个 worker 进程，
    那个进程的 ``active_connections`` 里也只有这一条连接。审查报告
    2026-11-14 的原始表述有误，已按实现纠正（坑表 P-95）。

策略（轻量版，零架构改动、零新增路由）：
    启动期探测部署意图（uvicorn 的 ``--workers`` 对应 ``WEB_CONCURRENCY``），
    默认 fail-fast 拒绝；确需多 worker 可设 ``ALLOW_MULTI_WORKER=1`` 放行并告警，
    但须自行承担状态分片后果。真正的根治是把上述状态外置到 Redis / 独立进程，
    属于 L9 契约拆分之后的架构决策，不在本批范围。
"""

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 按优先级排列：项目自定义变量 > 通用部署变量 > uvicorn/gunicorn 默认变量。
WORKER_ENV_KEYS = ("STOCK_SCANNER_WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY")
OPT_IN_ENV = "ALLOW_MULTI_WORKER"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def declared_worker_count(environ: Optional[Dict[str, str]] = None) -> int:
    """返回部署意图声明的 worker 数；未声明/非法值一律按单 worker 处理。

    传入 ``environ`` 是为了让判据可以离线测试，不依赖进程环境。
    """
    env = os.environ if environ is None else environ
    for key in WORKER_ENV_KEYS:
        raw = (env.get(key) or "").strip()
        if raw.isdigit():
            return max(1, int(raw))
    return 1


def _opted_in(environ: Dict[str, str]) -> bool:
    return (environ.get(OPT_IN_ENV) or "").strip().lower() in _TRUE_VALUES


def _rejection_message(count: int, key: str) -> str:
    return (
        f"[worker_guard] 拒绝启动：检测到多 worker 部署意图（{key} 声明 {count} 个）。\n"
        "本系统的任务状态/缓存/WS 连接均为进程内内存，多 worker 会导致：\n"
        "  - 扫描单飞失效：同一 preset 被并发扫描、缓存互相覆盖（B4 的锁只在进程内有效）\n"
        "  - 每个 worker 独立拉数据，数据源压力 ×N\n"
        "  - 客户端落到不同 worker 读到不同快照\n"
        "处理方式二选一：\n"
        "  1. 固定单 worker：uvicorn api:app --host 127.0.0.1 --port 8000\n"
        "     （不要传 --workers，也不要设 WEB_CONCURRENCY>1）\n"
        "  2. 确需多 worker：先做跨进程状态收口（见 docs/adr/0020 §后续），\n"
        "     临时可设 ALLOW_MULTI_WORKER=1 放行，风险自负\n"
    )


def verify_single_worker(environ: Optional[Dict[str, str]] = None) -> int:
    """校验 worker 形态：单 worker 静默通过；多 worker 默认拒绝，显式 opt-in 放行并告警。

    :return: 声明的 worker 数。
    :raises SystemExit: 多 worker 且未显式 opt-in 时拒绝启动（消息直接给部署修复步骤）。
    """
    env = os.environ if environ is None else environ
    count = declared_worker_count(env)
    if count <= 1:
        return count

    source = next(
        (k for k in WORKER_ENV_KEYS if (env.get(k) or "").strip().isdigit()),
        WORKER_ENV_KEYS[0],
    )
    message = _rejection_message(count, source)
    if _opted_in(env):
        logger.warning(message.replace("拒绝启动", "放行多 worker（风险自负）")
                       + "已设 ALLOW_MULTI_WORKER=1，进程内状态分片后果由部署方承担。")
        return count
    logger.error(message)
    raise SystemExit(message)
