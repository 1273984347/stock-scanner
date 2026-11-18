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

策略演进：
    - W 批（轻量版）：启动期只探测环境变量，默认 fail-fast 拒绝。
    - 2026-11-18 实测发现漏洞：``uvicorn api:app --workers 2`` 不写环境变量，
      守卫被一行命令绕过（两个 worker 都 startup complete）。P0 修复为同时
      探测 ``sys.argv``（命令行 > 环境变量），spawn 子进程 argv 保留该参数。
    - 完整版（进程间单飞）：由 ``lib/infra/process_leader.py`` 选主，
      单飞承担广播构造与状态写入；本模块退化为「探测 + 部署意图」职责。
"""

import logging
import os
import sys
from typing import Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 按优先级排列：命令行 > 项目自定义变量 > 通用部署变量 > uvicorn/gunicorn 默认变量。
# 2026-11-18 实测：uvicorn --workers N 不会写回环境变量，只读环境变量会被
# `uvicorn api:app --workers 2` 一行命令绕过。spawn 的子进程 argv 仍保留 --workers
# （已实测），故命令行探测在 worker 进程里同样有效。
WORKER_ENV_KEYS = ("STOCK_SCANNER_WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY")
OPT_IN_ENV = "ALLOW_MULTI_WORKER"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _worker_count_from_argv(argv: Sequence[str]) -> Optional[int]:
    """从命令行解析 worker 数；支持 ``--workers N`` / ``--workers=N`` / gunicorn ``-w N``。

    只接受纯数字（uvicorn 的 ``auto`` 等非法值降级为未声明），避免把
    ``--workers-config`` 这类前缀误当成 worker 数。
    """
    for i, arg in enumerate(argv):
        if arg in ("--workers", "-w"):
            if i + 1 < len(argv) and str(argv[i + 1]).strip().isdigit():
                return max(1, int(str(argv[i + 1]).strip()))
        elif arg.startswith("--workers="):
            raw = arg.split("=", 1)[1].strip()
            if raw.isdigit():
                return max(1, int(raw))
    return None


def declared_worker_count(environ: Optional[Dict[str, str]] = None,
                          argv: Optional[Sequence[str]] = None) -> int:
    """返回部署意图声明的 worker 数；未声明/非法值一律按单 worker 处理。

    优先级：命令行 > ``STOCK_SCANNER_WORKERS`` > ``UVICORN_WORKERS`` >
    ``WEB_CONCURRENCY``。传入 ``environ`` / ``argv`` 是为了让判据可以离线测试。
    """
    args = sys.argv if argv is None else argv
    from_cli = _worker_count_from_argv(args)
    if from_cli is not None:
        return from_cli
    env = os.environ if environ is None else environ
    for key in WORKER_ENV_KEYS:
        raw = (env.get(key) or "").strip()
        if raw.isdigit():
            return max(1, int(raw))
    return 1


def worker_count_source(environ: Optional[Dict[str, str]] = None,
                        argv: Optional[Sequence[str]] = None) -> Tuple[int, str]:
    """返回 (worker 数, 声明来源)，供错误信息精确指出是哪条配置触发的。"""
    args = sys.argv if argv is None else argv
    from_cli = _worker_count_from_argv(args)
    if from_cli is not None:
        return from_cli, "--workers 命令行参数"
    env = os.environ if environ is None else environ
    for key in WORKER_ENV_KEYS:
        raw = (env.get(key) or "").strip()
        if raw.isdigit():
            return max(1, int(raw)), key
    return 1, "默认（未声明）"


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


def verify_single_worker(environ: Optional[Dict[str, str]] = None,
                         argv: Optional[Sequence[str]] = None) -> int:
    """校验 worker 形态：单 worker 静默通过；多 worker 默认拒绝，显式 opt-in 放行并告警。

    P0 修复：同时探测命令行 --workers，堵住「清空环境变量 + --workers N」的绕过路径。
    :return: 声明的 worker 数。
    :raises SystemExit: 多 worker 且未显式 opt-in 时拒绝启动（消息直接给部署修复步骤）。
    """
    env = os.environ if environ is None else environ
    count, source = worker_count_source(env, argv)
    if count <= 1:
        return count

    message = _rejection_message(count, source)
    if _opted_in(env):
        logger.warning(
            f"[worker_guard] 放行多 worker（{source} 声明 {count} 个）："
            "已启用 lib/infra/process_leader.py 跨进程单飞——广播构造与扫描"
            "由文件锁选主，缓存/快照通过磁盘共享，WS 连接各进程独立推送。"
        )
        return count
    logger.error(message)
    raise SystemExit(message)
