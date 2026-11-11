#!/usr/bin/env python
"""Offload 开关前后响应 diff + 迁移等价性 —— RF-01 Phase 6 第 2 批取证（T4）。

Phase 6 承诺「只改调度方式，不改可观测结果」。本工具把这句话变成三层机器判据：

  1. **迁移等价**：`git show 523b353:routers/backtest.py` 取出改造前源码，
     把每个端点 `try:` 块的语句序列与现文件 `_compute_*` 函数体逐语句
     `ast.dump` 对比。任何「顺手优化」都会立刻判红（ADR-0003 D5 的纪律）。
     固定 commit 而非 HEAD，保证提交之后仍可复算。
  2. **off/on 等价**：同一进程、同一份确定性注入数据，分别以
     `FEATURE_ASYNC_OFFLOAD=0` 与 `=all` 调用 3 个端点函数，
     整份响应按字段顺序 JSON 序列化后逐字符比对。
  3. **自证真的换了线程**：把 `anyio.to_thread.run_sync` 换成真实
     `loop.run_in_executor` 并记录工作线程 ident —— off 侧计数 0，
     on 侧计数 1 且线程 ident 与主线程不同。
     两侧都没走线程池时 diff 也恒为 0 ⇒ 空判据（坑表 P-56 / T4）。

用法：
  python scripts/offload_switch_diff.py --check
  python scripts/offload_switch_diff.py --summary
输出为纯 ASCII（Windows CI stdout 默认 cp1252/cp936）。
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import functools
import json
import os
import pathlib
import subprocess
import sys
import threading
import types
from contextlib import ExitStack
from unittest.mock import patch

import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# `routers.common` 在模块导入期会启动 HS300 网络预取线程（见 common.py 底部）。
# 取证链路必须确定性、零触网，否则 off/on 两侧可能读到不同缓存 ⇒ 对拍失去意义。
# 与 scripts/contract_snapshot.py 的 collect() 同一处理口径。
os.environ["SKIP_HS300_PREFETCH"] = "1"

PRE_MIGRATION_COMMIT = "523b353"
SOURCE_REL = "routers/backtest.py"

# (consumer, endpoint key, old endpoint function, new sync compute function)
MIGRATIONS = (
    ("backtest", "POST /api/backtest", "run_backtest", "_compute_factor_backtest"),
    ("backtest_compare", "POST /api/backtest/compare", "compare_strategies", "_compute_compare"),
    ("walkforward", "POST /api/walkforward", "run_walkforward", "_compute_walkforward"),
)

FIXED_RAW = {
    "metrics": {"sharpe": 1.23, "total_return": 0.15, "max_drawdown": -0.08},
    "benchmark": {"sharpe": 0.80},
    "trade_count": 2,
    "exit_reasons": {"stop_loss": 1, "take_profit": 1},
    "cost_summary": {"commission": 12.5, "slippage": 3.25},
    "perf_df": None,
    "trades": [{"code": "000001", "pnl": 1.5}, {"code": "600519", "pnl": -0.4}],
}
FIXED_FACTOR_RESULT = {
    "metrics": {"sharpe": 1.1, "total_return": 0.12},
    "perf_df": None,
    "trade_count": 1,
}


# ---------------------------------------------------------------- 1. 迁移等价

def _old_source() -> str:
    return subprocess.run(
        ["git", "show", f"{PRE_MIGRATION_COMMIT}:{SOURCE_REL}"],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=True,
    ).stdout


def _find_fn(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise LookupError(f"function {name} not found")


def _old_try_body(fn: ast.AST) -> list[ast.stmt]:
    """改造前端点：`async with` -> `try` -> body。"""
    for node in ast.walk(fn):
        if isinstance(node, ast.AsyncWith):
            for inner in node.body:
                if isinstance(inner, ast.Try):
                    return inner.body
    raise LookupError("no async-with/try body in old endpoint")


def _new_fn_body(fn: ast.AST) -> list[ast.stmt]:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # skip the docstring added during migration
    return body


def check_migration() -> list[str]:
    try:
        old_src = _old_source()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return [f"cannot read pre-migration source at {PRE_MIGRATION_COMMIT}: {e}"]
    old_tree = ast.parse(old_src)
    new_tree = ast.parse((ROOT / SOURCE_REL).read_text(encoding="utf-8"))
    failures = []
    for consumer, key, old_name, new_name in MIGRATIONS:
        try:
            old_body = _old_try_body(_find_fn(old_tree, old_name))
            new_body = _new_fn_body(_find_fn(new_tree, new_name))
        except LookupError as e:
            failures.append(f"migration {consumer}: {e}")
            continue
        if len(old_body) != len(new_body):
            failures.append(f"migration {consumer}: statement count "
                            f"{len(old_body)} -> {len(new_body)}")
            continue
        for i, (a, b) in enumerate(zip(old_body, new_body)):
            if ast.dump(a) != ast.dump(b):
                failures.append(f"migration {consumer}: statement #{i} changed "
                                f"({type(a).__name__} vs {type(b).__name__})")
    return failures


# ---------------------------------------------------------------- 2. off/on 对拍

def _install_mocks(stack: ExitStack):
    """确定性注入：不触网、不跑真回测、不写文件。"""
    thread_id_box: list[int] = []

    def fake_unified_run(self, **kwargs):
        thread_id_box.append(threading.get_ident())
        return types.SimpleNamespace(raw=FIXED_RAW)

    def fake_factor(syms, days=250, preset="balanced", **kwargs):
        thread_id_box.append(threading.get_ident())
        return dict(FIXED_FACTOR_RESULT)

    def fake_batch(codes, days=120, source="auto"):
        return {c: pd.DataFrame({"date": ["2024-01-01", "2024-01-02"],
                                 "close": [10.0, 10.5]}) for c in codes}

    stack.enter_context(patch("backtest.unified.BacktestRunner.run", fake_unified_run))
    stack.enter_context(patch("backtest.core.run_factor_backtest", fake_factor))
    stack.enter_context(patch("routers.backtest._batch_get_stock_hist", fake_batch))
    stack.enter_context(patch("routers.backtest._get_orchestrator", lambda *a, **k: object()))
    stack.enter_context(patch("lib.strategy.walkforward.WalkForwardBacktest.run",
                              lambda self, **kwargs: []))
    stack.enter_context(patch("lib.strategy.walkforward.summarize_rounds",
                              lambda rounds: {"rounds": len(rounds)}))
    return thread_id_box


async def _call_endpoint(consumer: str, bt):
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


def _serialize(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=False, default=str)


async def _collect_all() -> dict:
    """单事件循环跑完所有采集，避免模块级 asyncio.Semaphore 跨 loop 绑定问题。"""
    from routers import backtest as bt
    import lib.infra.async_offload as offload

    results = {}
    main_ident = threading.get_ident()
    with ExitStack() as stack:
        thread_id_box = _install_mocks(stack)
        pool_counter = {"n": 0}
        worker_idents: list[int] = []

        async def real_thread_run_sync(func, *args, **kwargs):
            pool_counter["n"] += 1
            loop = asyncio.get_running_loop()

            def wrapper():
                worker_idents.append(threading.get_ident())
                return func(*args, **kwargs)

            return await loop.run_in_executor(None, wrapper)

        stack.enter_context(patch.object(offload.anyio.to_thread,
                                         "run_sync", real_thread_run_sync))

        for consumer, key, _old, _new in MIGRATIONS:
            for flag in ("0", "all"):
                os.environ["FEATURE_ASYNC_OFFLOAD"] = flag
                thread_id_box.clear()
                worker_idents.clear()
                pool_counter["n"] = 0
                response = await _call_endpoint(consumer, bt)
                results[(consumer, flag)] = {
                    "response": _serialize(response),
                    "pool_calls": pool_counter["n"],
                    "worker_idents": list(worker_idents),
                    "compute_idents": list(thread_id_box),
                    "main_ident": main_ident,
                }
    os.environ.pop("FEATURE_ASYNC_OFFLOAD", None)
    return results


def check_consumer_registry() -> list[str]:
    """新增消费者却没登记 / 没做对拍 => 判红（与 scoring_switch_diff 的消费者自证同构）。"""
    from lib.infra.async_offload import OFFLOAD_CONSUMERS

    found = set()
    for path in sorted((ROOT / "routers").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "run_blocking" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
    registered = set(OFFLOAD_CONSUMERS)
    covered = {m[0] for m in MIGRATIONS}
    failures = []
    if found != registered:
        failures.append(f"consumer registry mismatch: unregistered={sorted(found - registered)} "
                        f"registered-but-unused={sorted(registered - found)}")
    if registered != covered:
        failures.append(f"switch evidence missing for consumers: "
                        f"{sorted(registered - covered)}")
    return failures


def check_switch() -> tuple[list[str], dict]:
    results = asyncio.run(_collect_all())
    failures = []
    for consumer, key, _old, _new in MIGRATIONS:
        off = results[(consumer, "0")]
        on = results[(consumer, "all")]
        if off["response"] != on["response"]:
            failures.append(f"switch diff {key}: response differs off vs on")
        if off["pool_calls"] != 0:
            failures.append(f"switch diff {key}: off-side used thread pool")
        if on["pool_calls"] != 1:
            failures.append(f"switch diff {key}: on-side did NOT use thread pool "
                            f"(count={on['pool_calls']})")
        if not on["worker_idents"] or on["worker_idents"][0] == on["main_ident"]:
            failures.append(f"switch diff {key}: on-side ran on the main thread")
        if any(i != off["main_ident"] for i in off["compute_idents"]):
            failures.append(f"switch diff {key}: off-side compute left the main thread")
    return failures, results


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    migration = check_migration()
    registry = check_consumer_registry()
    switch_failures, results = check_switch()
    failures = migration + registry + switch_failures

    if args.summary:
        print(f"[INFO] pre-migration commit: {PRE_MIGRATION_COMMIT}")
        for consumer, key, old, new in MIGRATIONS:
            print(f"       {consumer:18s} {key:32s} {old} -> {new}")
        for (consumer, flag), rec in sorted(results.items()):
            print(f"       {consumer:18s} flag={flag:3s} pool_calls={rec['pool_calls']} "
                  f"response_bytes={len(rec['response'])}")
        return 0

    if failures:
        print(f"[FAIL] offload switch: {len(failures)} problem(s)")
        for f in failures[:30]:
            print("      " + f)
        return 1
    print(f"[PASS] offload switch: {len(MIGRATIONS)} endpoints, "
          f"migration AST equivalent, off/on responses identical (field order kept), "
          f"thread pool off=0 on=1 with different worker thread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
