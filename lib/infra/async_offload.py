"""RF-01 Phase 6 —— 阻塞调用的唯一 offload 入口。

背景：68 个 `/api/*` 端点全部是 `async def`，而回测、批量取数、pandas 计算、
文件读写等同步操作直接在函数体内执行，会占住事件循环。Phase 6 要把这些
阻塞段移进线程池，且**不改变任何响应内容**。

设计（RULES §1.7 T1–T4）：

* 灰度判定**只在这里**。消费者不得自读 `FEATURE_ASYNC_OFFLOAD` /
  调 `async_offload_enabled()`（与评分域 S8 同构，有 AST 硬守卫）。
* `off`：**同步直调** `fn(*args, **kwargs)`。与改造前完全等价 ——
  同一线程、同一返回值对象、异常原样传播，不做任何包装。
* `on`（**默认**，评审 P0-1 2026-11-20 翻转）：`anyio.to_thread.run_sync` 把 `fn`
  放进线程池，端点保持 `async def` 签名不变（禁止改成 `def` 来「顺手」走线程池，
  那会静默改变调度语义）。
* 不做重试、不做超时包装、不吞异常：工作线程里的异常由 anyio 原样重新抛出，
  端点的 `try/except` 结构与改造前一致。

回滚：`FEATURE_ASYNC_OFFLOAD=0` **显式置 0** 即回到同步直调路径（注意：删除环境变量
回到的是默认 `all`，不是 off——翻转记录见 `docs/audit/architecture-review-2026-11-20.md`
批次 C2，前置条件 audit 锁 + WAL 已落地）。
"""

from __future__ import annotations

import functools
from typing import Callable, TypeVar

import anyio.to_thread

from config import async_offload_enabled

T = TypeVar("T")

# 已接入 offload 的消费者（端点粒度，按 T4 逐个取证）。
# 新增消费者必须同时补 off/on 对拍与线程池自证，否则对拍脚本会判红。
OFFLOAD_CONSUMERS: tuple[str, ...] = (
    "backtest",
    "backtest_compare",
    "walkforward",
    # Phase 6 第 4 批（Tier A 候选，按阻塞调用数 Top5 切入）
    "factor_eval",
    "factor_scatter",
    "kline",
    "risk",
    "export_config",
    # Phase 6 第 5 批（Tier A 候选，blocking ≥4；/api/dashboard 因 try 体含
    # await（asyncio.to_thread）无法逐字搬迁，留待单独处理）
    "factor_contribution",
    "market",
    "positions",
    "risk_exposure",
    "stock",
    # Phase 6 第 6 批（Tier A 候选，blocking ≥2 且不含 await）
    "signals",
    "exposures",
    "position",
    "regime_history",
    "score",
    "sector_flow",
    # Phase 6 第 7 批（Tier A 候选 1 阻塞调用，最后一批；dashboard 除外 —— ADR-0013 转 RF-02）
    "attribution",
    "audit",
    "audit_detail",
    "export_trades",
    "export_trades_excel",
    "export_pdf",
    "factor_ic_history",
    "factor_weights",
    "notifications",
    "notification_stats",
    "position_distribution",
    "risk_correlation",
    "sector_heatmap",
    "signals_accuracy_trend",
    "strategy_ranking",
    "strategy_recommend",
    "trade_history",
    "ab_test",
)


async def run_blocking(consumer: str, fn: Callable[..., T], *args, **kwargs) -> T:
    """按消费者灰度执行一段同步阻塞代码。

    Args:
        consumer: 消费者名，必须在 OFFLOAD_CONSUMERS 里登记。
        fn: 同步函数（阻塞段原样搬出的纯计算体）。
        *args, **kwargs: 原样透传给 fn，不做任何转换。
    """
    if not async_offload_enabled(consumer):
        return fn(*args, **kwargs)
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
