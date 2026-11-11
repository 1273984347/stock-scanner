#!/usr/bin/env python
"""并发压测与前端路径冻结 —— RF-01 Phase 6 第 3 批（L10）。

计划 §三 Phase 6 验证方式要求：
  「20 并发 × 慢端点（/api/scan、/api/backtest），P95 不劣化」
  「18 条前端路径（ADR-0004 修正为 36 条）× 阻塞端点全集，新旧各跑」

本工具把这两条变成**确定性、零触网、低 CPU** 的机器判据：

  * 用 `time.sleep(BLOCK_SECONDS)` 在 mock 里模拟真实阻塞 IO。sleep 不占 CPU，
    且用户设备有「持续重负载保护性关机」约束，故刻意用短 small-batch（3 轮 × 20 并发），
    不跑真实回测、不挂满负载。
  * off 侧同步直调会占住事件循环 ⇒ 20 个请求串行完成；
    on 侧走 anyio 线程池 ⇒ 可并行。测的正是「事件循环是否被占住」。
  * 稳定性口径按坑表 P-09：报 3 轮的 max/min，不用小样本 CV。
  * 36 条前端路径**不逐条实跑**：全量实跑会触网 / 写缓存 / 触发后台预取，
    破坏 off/on 对照的确定性（坑表 P-69）。改为 L2 契约快照 + L7 归属表
    成员+数量双校验，并验证 flag 切换前后 `app.openapi()` 完全一致。

用法：
  python scripts/concurrency_probe.py --check
  python scripts/concurrency_probe.py --summary
输出为纯 ASCII（Windows CI stdout 默认 cp1252/cp936）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import pathlib
import statistics
import sys
import threading
import time
import types
from contextlib import ExitStack
from unittest.mock import patch

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# routers.common 在导入期会启动 HS300 网络预取线程（坑表 P-69）。
os.environ["SKIP_HS300_PREFETCH"] = "1"

CONTRACTS = ROOT / "docs" / "refactor" / "contracts"
FRONTEND_FILE = CONTRACTS / "frontend-api-paths.json"
CONTRACT_FILE = CONTRACTS / "route-contract-baseline.json"

CONCURRENCY = 20
ROUNDS = 3
BLOCK_SECONDS = 0.02
P95_TOLERANCE = 1.10  # on 侧允许 10% 噪声；实测应远低于 off 侧

ENDPOINTS = (
    ("backtest", "POST /api/backtest"),
    ("backtest_compare", "POST /api/backtest/compare"),
    ("walkforward", "POST /api/walkforward"),
)

FIXED_RAW = {
    "metrics": {"sharpe": 1.23}, "benchmark": {"sharpe": 0.8},
    "trade_count": 2, "exit_reasons": {}, "cost_summary": {},
    "perf_df": None, "trades": [],
}
FIXED_FACTOR = {"metrics": {"sharpe": 1.1}, "perf_df": None, "trade_count": 1}


def _p95(values: list[float]) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def _install_mocks(stack: ExitStack) -> list[int]:
    """确定性注入：每个端点的一次请求约 BLOCK_SECONDS 的阻塞，零触网。

    返回执行过计算的工作线程 ident 列表，用于自证 on 侧真的换了线程。
    不替换 `anyio.to_thread.run_sync` —— 压测必须跑生产路径的线程池。
    """
    import pandas as pd
    worker_idents: list[int] = []

    def fake_unified_run(self, **kwargs):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return types.SimpleNamespace(raw=FIXED_RAW)

    def fake_factor(syms, days=250, preset="balanced", **kwargs):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS / 3)  # compare 一次请求调 3 个 preset
        return dict(FIXED_FACTOR)

    def fake_batch(codes, days=120, source="auto"):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return {c: pd.DataFrame({"date": ["2024-01-01"],
                                 "close": [10.0]}) for c in codes}

    stack.enter_context(patch("backtest.unified.BacktestRunner.run", fake_unified_run))
    stack.enter_context(patch("backtest.core.run_factor_backtest", fake_factor))
    stack.enter_context(patch("routers.backtest._batch_get_stock_hist", fake_batch))
    stack.enter_context(patch("routers.backtest._get_orchestrator", lambda *a, **k: object()))
    stack.enter_context(patch("lib.strategy.walkforward.WalkForwardBacktest.run",
                              lambda self, **kwargs: []))
    stack.enter_context(patch("lib.strategy.walkforward.summarize_rounds",
                              lambda rounds: {"rounds": len(rounds)}))
    return worker_idents


async def _call(consumer: str, bt):
    if consumer == "backtest":
        return await bt.run_backtest(
            symbols="000001,600519,000858", days=250,
            stop_loss_atr_mult=2.0, take_profit_atr_mult=4.0,
            max_hold_days=10, confidence_threshold=0.3,
            risk_per_trade_pct=0.5, preset="balanced", _=None)
    if consumer == "backtest_compare":
        return await bt.compare_strategies(symbols="000001,600519", days=250, _=None)
    return await bt.run_walkforward(
        symbols="000001,600519", train_days=120,
        validation_days=30, step_days=30, _=None)


async def _one_round(consumer: str, bt, flag: str, worker_idents: list[int]):
    """一轮 20 并发；跑真实 anyio 线程池，不替换执行器。

    计时口径 = **从批次发起算起的完成延迟**（客户端视角），不是协程自身耗时。
    原因（坑表 P-70）：off 侧同步直调会阻塞事件循环，后面的协程要等前面跑完
    才首次被调度，`t0` 设得晚 ⇒ 单请求耗时虚低（~20ms）；
    on 侧让出 loop 后 20 个协程早早设 `t0`，等信号量的时间全被计入 ⇒ 虚高（~209ms）。
    用「协程内计时」会得到「offload 慢 10 倍」的假结论。
    """
    os.environ["FEATURE_ASYNC_OFFLOAD"] = flag
    main_ident = threading.get_ident()
    worker_idents.clear()
    shapes: set[str] = set()
    batch_start = time.perf_counter()

    async def one():
        resp = await _call(consumer, bt)
        elapsed = (time.perf_counter() - batch_start) * 1000.0
        assert isinstance(resp, dict) and resp, "empty/corrupt response"
        shapes.add(json.dumps(resp, sort_keys=True, default=str))
        return elapsed

    durations = await asyncio.gather(*[one() for _ in range(CONCURRENCY)])
    idents = set(worker_idents)
    return (list(durations), idents == {main_ident},
            any(i != main_ident for i in idents), shapes)


def _frontend_freeze() -> tuple[list[str], dict]:
    """36 条前端路径：与运行时契约成员+数量双校验 + off/on OpenAPI 冻结。"""
    fe = json.loads(FRONTEND_FILE.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    declared = {r["path"] for r in contract["routes"] if r.get("category") == "api"}
    paths = fe["normalized_paths"]
    failures = []
    if len(paths) != 36:
        failures.append(f"frontend path count: {len(paths)} (expected 36)")
    if not set(paths) <= declared:
        failures.append(f"unmapped frontend paths: {sorted(set(paths) - declared)}")
    if fe["counts"]["unmapped"] != 0:
        failures.append(f"frontend unmapped={fe['counts']['unmapped']}")

    from api import app
    # FastAPI 会缓存 app.openapi_schema；不清缓存的话 off/on 拿到的是同一个对象，
    # 比较恒等 = 空判据（坑表 P-54）。每次强制重新生成。
    os.environ["FEATURE_ASYNC_OFFLOAD"] = "0"
    app.openapi_schema = None
    off = json.dumps(app.openapi(), sort_keys=True, default=str)
    os.environ["FEATURE_ASYNC_OFFLOAD"] = "all"
    app.openapi_schema = None
    on = json.dumps(app.openapi(), sort_keys=True, default=str)
    os.environ.pop("FEATURE_ASYNC_OFFLOAD", None)
    if off != on:
        failures.append("OpenAPI differs between off and on")
    return failures, {"paths": len(paths), "declared": len(declared),
                      "openapi_frozen": off == on}


async def _probe_async(bt, report: dict, failures: list[str]) -> None:
    """单事件循环跑完全部轮次：模块级 asyncio.Semaphore 不能跨 loop 复用。"""
    with ExitStack() as stack:
        worker_idents = _install_mocks(stack)
        for consumer, key in ENDPOINTS:
            off_rounds, on_rounds = [], []
            off_on_main, on_worker_seen = 0, 0
            off_shapes, on_shapes = set(), set()
            for _ in range(ROUNDS):
                off_dur, off_main, _, sh_off = await _one_round(
                    consumer, bt, "0", worker_idents)
                on_dur, _, on_worker, sh_on = await _one_round(
                    consumer, bt, "all", worker_idents)
                off_rounds.append(_p95(off_dur))
                on_rounds.append(_p95(on_dur))
                off_on_main += int(off_main)
                on_worker_seen += int(on_worker)
                off_shapes |= sh_off
                on_shapes |= sh_on
            off_p95 = statistics.median(off_rounds)
            on_p95 = statistics.median(on_rounds)
            ratio = on_p95 / off_p95 if off_p95 else float("inf")
            report["endpoints"][consumer] = {
                "key": key,
                "off_p95_ms": round(off_p95, 2), "on_p95_ms": round(on_p95, 2),
                "ratio": round(ratio, 3),
                "off_round_max_min": [round(max(off_rounds), 2), round(min(off_rounds), 2)],
                "on_round_max_min": [round(max(on_rounds), 2), round(min(on_rounds), 2)],
                "off_all_on_main_rounds": off_on_main,
                "on_worker_rounds": on_worker_seen,
                "response_shapes": len(off_shapes | on_shapes),
            }
            if on_p95 > off_p95 * P95_TOLERANCE:
                failures.append(f"P95 REGRESSION {key}: off={off_p95:.1f}ms "
                                f"on={on_p95:.1f}ms ratio={ratio:.2f}")
            if on_worker_seen != ROUNDS:
                failures.append(f"{key}: on-side did not use a worker thread in every "
                                f"round ({on_worker_seen}/{ROUNDS}) -- empty judgement")
            if off_on_main != ROUNDS:
                failures.append(f"{key}: off-side left the main thread "
                                f"({ROUNDS - off_on_main} rounds)")
            if off_shapes != on_shapes or len(off_shapes) != 1:
                failures.append(f"{key}: response shape differs off vs on "
                                f"({len(off_shapes)}/{len(on_shapes)} shapes)")


def probe() -> tuple[list[str], dict]:
    from routers import backtest as bt

    failures: list[str] = []
    report: dict = {"concurrency": CONCURRENCY, "rounds": ROUNDS,
                    "block_ms": BLOCK_SECONDS * 1000, "endpoints": {}}
    asyncio.run(_probe_async(bt, report, failures))

    fe_failures, fe_report = _frontend_freeze()
    failures += fe_failures
    report["frontend"] = fe_report
    os.environ.pop("FEATURE_ASYNC_OFFLOAD", None)
    return failures, report


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    failures, report = probe()
    if args.summary:
        print(f"[INFO] concurrency={CONCURRENCY} rounds={ROUNDS} "
              f"simulated_block={BLOCK_SECONDS * 1000:.0f}ms")
        for consumer, r in report["endpoints"].items():
            print(f"       {consumer:18s} off_p95={r['off_p95_ms']:8.2f}ms "
                  f"on_p95={r['on_p95_ms']:8.2f}ms ratio={r['ratio']:.3f} "
                  f"off[max/min]={r['off_round_max_min']} on[max/min]={r['on_round_max_min']} "
                  f"off_main_rounds={r['off_all_on_main_rounds']} "
                  f"on_worker_rounds={r['on_worker_rounds']} "
                  f"shapes={r['response_shapes']}")
        print(f"       frontend paths={report['frontend']['paths']} "
              f"declared={report['frontend']['declared']} "
              f"openapi_frozen={report['frontend']['openapi_frozen']}")
        return 0

    if failures:
        print(f"[FAIL] concurrency: {len(failures)} problem(s)")
        for f in failures[:30]:
            print("      " + f)
        return 1
    improved = sum(1 for r in report["endpoints"].values() if r["ratio"] < 1.0)
    print(f"[PASS] concurrency: {len(ENDPOINTS)} endpoints x {CONCURRENCY} concurrent "
          f"x {ROUNDS} rounds, P95 no regression (tolerance {P95_TOLERANCE:.2f}x), "
          f"{improved}/{len(ENDPOINTS)} improved; "
          f"frontend {report['frontend']['paths']} paths frozen, off/on OpenAPI identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
