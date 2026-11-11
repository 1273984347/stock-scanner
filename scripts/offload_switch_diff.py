#!/usr/bin/env python
"""Offload 开关前后响应 diff + 迁移等价性 —— RF-01 Phase 6 取证（T4）。

Phase 6 承诺「只改调度方式，不改可观测结果」。本工具把这句话变成三层机器判据：

  1. **迁移等价**：每条迁移记录改造前源码（固定 commit）中 `try:` 块语句序列，
     与现文件 `_compute_*` 同步函数体逐语句 `ast.dump` 对比。任何「顺手优化」都会
     立刻判红（ADR-0003 D5）。每条迁移自带 `source_rel` + `pre_commit`，
     提交之后仍可复算（backtest 固定 523b353；第 4 批固定 77bc366；第 5 批固定 6d0e434）。
     无 try 块的端点（如 /api/market、/api/stock/{code}）按 `no_try_guard` 剥掉
     函数体前 N 条快守卫语句（缓存命中分支 / 参数校验）后对齐。
  2. **off/on 等价**：同一进程、同一份确定性注入数据，分别以
     `FEATURE_ASYNC_OFFLOAD=0` 与 `=all` 调用端点函数，
     整份响应按字段顺序 JSON 序列化后逐字符比对（JSONResponse 取 `.body`）。
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
import datetime as _dt
import functools
import json
import os
import pathlib
import subprocess
import sys
import threading
import types
from collections import namedtuple
from contextlib import ExitStack
from unittest.mock import Mock, patch

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

# 每条迁移：消费者名 / 端点 key / 改造前端点函数名 / 现同步计算函数名 /
# 源码相对路径 / 改造前基线 commit（未 offload 状态）。
# no_try_guard：端点无 try 块时，旧函数体剥掉 docstring 后再剥掉前 N 条
# 快守卫语句（缓存命中分支 / `_validate_stock_code` 校验）即为搬迁体。
Migration = namedtuple("Migration",
                       "consumer key old_fn new_fn source_rel pre_commit no_try_guard",
                       defaults=(0,))

MIGRATIONS = (
    Migration("backtest", "POST /api/backtest", "run_backtest", "_compute_factor_backtest",
              "routers/backtest.py", "523b353"),
    Migration("backtest_compare", "POST /api/backtest/compare", "compare_strategies", "_compute_compare",
              "routers/backtest.py", "523b353"),
    Migration("walkforward", "POST /api/walkforward", "run_walkforward", "_compute_walkforward",
              "routers/backtest.py", "523b353"),
    # Phase 6 第 4 批 Tier A 候选 Top5（基线 commit 77bc366 = batch4 起点，均未 offload）
    Migration("factor_eval", "GET /api/factor-eval", "factor_evaluation", "_compute_factor_evaluation",
              "routers/factor.py", "77bc366"),
    Migration("factor_scatter", "GET /api/factor-scatter", "factor_scatter", "_compute_factor_scatter",
              "routers/factor.py", "77bc366"),
    Migration("kline", "GET /api/kline/{code}", "kline_data", "_compute_kline",
              "routers/market.py", "77bc366"),
    Migration("risk", "GET /api/risk", "risk_assessment", "_compute_risk",
              "routers/risk.py", "77bc366"),
    Migration("export_config", "GET /api/export/config", "export_config", "_compute_export_config",
              "routers/export.py", "77bc366"),
    # Phase 6 第 5 批 Tier A 候选（基线 commit 6d0e434 = batch5 起点，均未 offload；
    # /api/dashboard try 体含 await，无法逐字搬进同步函数，不在本批）
    Migration("factor_contribution", "GET /api/factor-contribution", "factor_contribution",
              "_compute_factor_contribution", "routers/factor.py", "6d0e434"),
    Migration("market", "GET /api/market", "market_overview", "_compute_market",
              "routers/market.py", "6d0e434", 2),
    Migration("positions", "GET /api/positions", "positions", "_compute_positions",
              "routers/portfolio.py", "6d0e434"),
    Migration("risk_exposure", "GET /api/risk-exposure", "risk_exposure", "_compute_risk_exposure",
              "routers/risk.py", "6d0e434"),
    Migration("stock", "GET /api/stock/{code}", "stock_detail", "_compute_stock_detail",
              "routers/stock.py", "6d0e434", 1),
)

# 冻结时间，消除 off/on 两侧 datetime.now() 时间戳差异（属测试伪影，非 offload 引入）
FIXED_DT = _dt.datetime(2026, 1, 1, 0, 0, 0)

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

def _old_source(rel: str, commit: str) -> str:
    return subprocess.run(
        ["git", "show", f"{commit}:{rel}"],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=True,
    ).stdout


def _find_fn(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise LookupError(f"function {name} not found")


def _parent_map(node):
    parents = {}
    for n in ast.walk(node):
        for child in ast.iter_child_nodes(n):
            parents[child] = n
    return parents


def _old_try_body(fn: ast.AST) -> list[ast.stmt]:
    """改造前端点：取最外层 `try:` 块的 body。
    最外层 try 的父节点是函数定义或 async-with（而非 for/if 内的内层 try）。"""
    parents = _parent_map(fn)
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    if not tries:
        raise LookupError("no try block in old endpoint")
    def depth(n):
        d, cur = 0, n
        while cur in parents:
            cur = parents[cur]
            d += 1
        return d
    tries.sort(key=depth)
    return tries[0].body


def _strip_leading_imports(stmts):
    """原 try 体与现函数体的 import 可能位置不同（函数内 vs try 内），
    统一剥掉前导 import 后再比对，只关心计算逻辑是否逐字一致。"""
    i = 0
    while i < len(stmts) and isinstance(stmts[i], (ast.Import, ast.ImportFrom)):
        i += 1
    return stmts[i:]


def _new_fn_body(fn: ast.AST) -> list[ast.stmt]:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # skip the docstring added during migration
    return _strip_leading_imports(body)


def _body_after_guard(fn: ast.AST, guard_stmts: int) -> list[ast.stmt]:
    """无 try 端点：函数体剥掉 docstring 后，再剥掉前 guard_stmts 条快守卫语句
    （缓存命中分支 / 参数校验），剩余即原样搬进 `_compute_*` 的阻塞体。"""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # docstring
    return body[guard_stmts:]


def _old_body(m: Migration, fn: ast.AST) -> list[ast.stmt]:
    if m.no_try_guard:
        return _strip_leading_imports(_body_after_guard(fn, m.no_try_guard))
    return _strip_leading_imports(_old_try_body(fn))


def check_migration() -> list[str]:
    failures = []
    for m in MIGRATIONS:
        try:
            old_src = _old_source(m.source_rel, m.pre_commit)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            failures.append(f"migration {m.consumer}: cannot read {m.pre_commit}:{m.source_rel}: {e}")
            continue
        old_tree = ast.parse(old_src)
        new_tree = ast.parse((ROOT / m.source_rel).read_text(encoding="utf-8"))
        try:
            old_body = _old_body(m, _find_fn(old_tree, m.old_fn))
            new_body = _new_fn_body(_find_fn(new_tree, m.new_fn))
        except LookupError as e:
            failures.append(f"migration {m.consumer}: {e}")
            continue
        if len(old_body) != len(new_body):
            failures.append(f"migration {m.consumer}: statement count "
                            f"{len(old_body)} -> {len(new_body)}")
            continue
        for i, (a, b) in enumerate(zip(old_body, new_body)):
            if ast.dump(a) != ast.dump(b):
                failures.append(f"migration {m.consumer}: statement #{i} changed "
                                f"({type(a).__name__} vs {type(b).__name__})")
    return failures


# ---------------------------------------------------------------- 2. off/on 对拍

class _FakeOrch:
    """factor / factor-scatter 共用的假 orchestrator：零网络、确定性。"""
    def get_realtime_quotes(self):
        return pd.DataFrame({
            "代码": [f"{i:06d}" for i in range(1, 7)],
            "成交额": [100.0] * 6,
            "涨跌幅": [0.01 * i for i in range(1, 7)],
        })
    def get_stock_hist_multi_source(self, code, days=120, source="auto"):
        return pd.DataFrame({"涨跌幅": [0.02] * 65})


def _fake_compute_all_signals(hist):
    return {"factors": {"volume_ratio": 1.0, "adx": 2.0, "rsi": 3.0}}


class _FakeAlpha:
    def compute_ic(self, a, b):
        return {"ic": 0.1, "rank_ic": 0.2, "rank_ic_pvalue": 0.3}


def _fake_get_stock_hist(code, days=120, **kwargs):
    n = 65
    return pd.DataFrame({
        "date": [f"2024-01-{i % 28 + 1:02d}" for i in range(1, n + 1)],
        "open": [10.0 + (i % 5) for i in range(n)],
        "high": [11.0 + (i % 5) for i in range(n)],
        "low": [9.0 + (i % 5) for i in range(n)],
        "close": [10.5 + (i % 5) for i in range(n)],
        "volume": [1000 + i for i in range(n)],
    })


def _fake_ema(series, n):
    return pd.Series(list(series), dtype="float64")


class _FakeOrchRisk:
    def get_market_sentiment(self):
        return object()


class _FakeMacro:
    state = "normal"
    regime = "stable"
    position_multiplier = 1.0
    reasoning = "test"


class _FakeRiskMgr:
    def __init__(self, *a, **k):
        pass
    def get_risk_budget(self, regime, mult):
        return {"regime": regime, "budget": 0.5}
    def compute_var_cvar(self, market_data, tickers, weights, total_value):
        return {"var_95": -0.02, "cvar_95": -0.03}
    def compute_correlation_matrix(self, market_data, tickers):
        return {"avg_correlation": 0.3, "diversification_score": 0.7,
                "high_corr_pairs": []}


class _FakePositionMgr:
    # risk_exposure 需要：非空持仓表（risk 端点不用该属性，互不影响）
    positions = {
        "000001": types.SimpleNamespace(weight=0.6, shares=1000.0, buy_price=10.0),
        "600519": types.SimpleNamespace(weight=0.4, shares=100.0, buy_price=1500.0),
    }
    def __init__(self, *a, **k):
        pass
    def get_portfolio_summary(self, portfolio):
        return {"total_value": 1_000_000}
    def check_stop_loss(self, prices):
        return [{"code": "000001", "level": "warning", "message": "接近止损价"}]


class _FakeTracker:
    def update_actual_returns(self):
        pass
    def get_recent_signals(self, days=30):
        return [
            {"source": "volume_ratio", "actual_return_5d": 0.05},
            {"source": "volume_ratio", "actual_return_5d": -0.02},
            {"source": "adx", "actual_return_5d": 0.03},
            {"source": "adx", "actual_return_5d": 0.0},
        ]


class _FakeOrchMarket:
    def get_market_sentiment(self):
        return 0.5


class _FakeQuotesOrch:
    def get_realtime_quotes(self):
        return pd.DataFrame({
            "代码": ["000001", "600519"],
            "最新价": [10.0, 1500.0],
        })


class _FakeOrchStock:
    def get_stock_hist(self, code, days=180):
        return _fake_get_stock_hist(code, days=days)


def _install_mocks(stack: ExitStack):
    """确定性注入：不触网、不跑真回测/真风控、不写缓存。"""
    thread_id_box: list[int] = []

    # ---- backtest（3 个，基线 523b353）----
    def fake_unified_run(self, **kwargs):
        thread_id_box.append(threading.get_ident())
        return types.SimpleNamespace(raw=FIXED_RAW)
    def fake_factor(syms, days=250, preset="balanced", **kwargs):
        thread_id_box.append(threading.get_ident())
        return dict(FIXED_FACTOR_RESULT)
    def fake_batch(codes, days=120, source="auto"):
        return {c: pd.DataFrame({"date": ["2024-01-01", "2024-01-02"],
                                 "close": [10.0, 10.5]}) for c in codes}
    def fake_risk_batch(codes, days=120, **kwargs):
        # 第 5 批 risk_exposure 需要：非空行情（risk 端点 tickers 为空，不触此 mock）
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

    # ---- factor（factor-eval + factor-scatter）----
    orch_factor = _FakeOrch()
    stack.enter_context(patch("routers.factor._get_orchestrator", lambda *a, **k: orch_factor))
    stack.enter_context(patch("factors.compute_all_signals", _fake_compute_all_signals))
    stack.enter_context(patch("lib.scoring.alpha.AlphaSynthesizer", _FakeAlpha))
    stack.enter_context(patch("routers.factor._cache_get", lambda *a, **k: None))
    stack.enter_context(patch("routers.factor._cache_set", lambda *a, **k: None))

    # ---- market（kline）----
    stack.enter_context(patch("lib.data.data_layer.get_stock_hist", _fake_get_stock_hist))
    stack.enter_context(patch("lib.scoring.technical_signals.ema", _fake_ema))
    stack.enter_context(patch("backtest.utils.compute_atr", lambda df: 1.0))
    stack.enter_context(patch("routers.market._cache_get", lambda *a, **k: None))
    stack.enter_context(patch("routers.market._cache_set", lambda *a, **k: None))

    # ---- risk ----
    orch_risk = _FakeOrchRisk()
    stack.enter_context(patch("routers.risk._get_orchestrator", lambda *a, **k: orch_risk))
    stack.enter_context(patch("factors.evaluate_macro_gate", lambda s: _FakeMacro()))
    stack.enter_context(patch("lib.risk.risk_manager.VolCorrRiskManager", _FakeRiskMgr))
    stack.enter_context(patch("lib.risk.position_manager.PositionManager", _FakePositionMgr))
    stack.enter_context(patch("routers.risk._batch_get_stock_hist", fake_risk_batch))
    stack.enter_context(patch("routers.risk._run_stress_tests",
                              lambda: [{"name": "x", "result": "pass"}]))
    stack.enter_context(patch("routers.risk._cache_get",
                              lambda key, **k: None if key in ("risk", "risk_exposure") else {}))
    stack.enter_context(patch("routers.risk._cache_set", lambda *a, **k: None))

    # ---- export（export_config）----
    stack.enter_context(patch("config.get_config",
                              lambda preset: {"weights": [0.5, 0.5], "meta": {"a": 1},
                                              "nested": {"b": 2}}))
    stack.enter_context(patch("routers.export._cache_get", lambda *a, **k: None))
    stack.enter_context(patch("routers.export._cache_set", lambda *a, **k: None))

    # ---- Phase 6 第 5 批（factor-contribution / market / positions / risk-exposure / stock）----
    stack.enter_context(patch("lib.scoring.signal_tracker.SignalTracker", _FakeTracker))
    stack.enter_context(patch("routers.market._get_orchestrator",
                              lambda *a, **k: _FakeOrchMarket()))
    stack.enter_context(patch("routers.portfolio._get_orchestrator",
                              lambda *a, **k: _FakeQuotesOrch()))
    stack.enter_context(patch("routers.stock._get_orchestrator",
                              lambda *a, **k: _FakeOrchStock()))
    stack.enter_context(patch("lib.scoring.technical_signals.calc_composite_verdict",
                              lambda hist: {"verdict": "buy",
                                            "signals": {"ema_above": True},
                                            "summary": "ok"}))

    return thread_id_box


def _normalize_response(obj) -> str:
    """JSONResponse 取 `.body` 字节；普通 dict/list 直接序列化。off/on 一致比对用。"""
    if hasattr(obj, "body"):
        return obj.body.decode("utf-8")
    return json.dumps(obj, ensure_ascii=False, sort_keys=False, default=str)


async def _call_endpoint(consumer, bt_bt, bt_factor, bt_market, bt_risk, bt_export,
                         bt_portfolio, bt_stock):
    if consumer == "backtest":
        return await bt_bt.run_backtest(
            symbols="000001,600519,000858", days=250,
            stop_loss_atr_mult=2.0, take_profit_atr_mult=4.0,
            max_hold_days=10, confidence_threshold=0.3,
            risk_per_trade_pct=0.5, preset="balanced", _=None)
    if consumer == "backtest_compare":
        return await bt_bt.compare_strategies(symbols="000001,600519", days=250, _=None)
    if consumer == "walkforward":
        return await bt_bt.run_walkforward(
            symbols="000001,600519", train_days=120,
            validation_days=30, step_days=30, _=None)
    if consumer == "factor_eval":
        return await bt_factor.factor_evaluation(n_stocks=30)
    if consumer == "factor_scatter":
        return await bt_factor.factor_scatter(x_factor="volume_ratio", y_factor="adx", n_stocks=30)
    if consumer == "kline":
        return await bt_market.kline_data(code="000001", days=120)
    if consumer == "risk":
        return await bt_risk.risk_assessment(_=None)
    if consumer == "export_config":
        return await bt_export.export_config(preset="balanced")
    if consumer == "factor_contribution":
        return await bt_factor.factor_contribution(days=30)
    if consumer == "market":
        return await bt_market.market_overview(_=None)
    if consumer == "positions":
        return await bt_portfolio.positions(_=None)
    if consumer == "risk_exposure":
        return await bt_risk.risk_exposure()
    if consumer == "stock":
        return await bt_stock.stock_detail(code="000001", _=None)
    raise ValueError(f"unknown consumer {consumer}")


async def _collect_all() -> dict:
    """单事件循环跑完所有采集，避免模块级 asyncio.Semaphore 跨 loop 绑定问题。"""
    from routers import backtest as bt_bt
    from routers import factor as bt_factor
    from routers import market as bt_market
    from routers import risk as bt_risk
    from routers import export as bt_export
    from routers import portfolio as bt_portfolio
    from routers import stock as bt_stock
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
        # datetime 是 C 扩展不可变类型，patch("datetime.datetime.now") 会抛
        # TypeError: cannot set 'now' attribute of immutable type。改用 Mock(wraps=...)
        # 钉住 .now，并 patch 各 router 命名空间自己的 datetime 名字绑定
        # （router 经 `from .common import *` 持有各自的 datetime 名字）。
        frozen_dt = Mock(wraps=_dt.datetime)
        frozen_dt.now.return_value = FIXED_DT
        frozen_dt.utcnow.return_value = FIXED_DT
        for _ns in ("routers.factor", "routers.risk", "routers.export",
                    "routers.common"):
            try:
                stack.enter_context(patch(_ns + ".datetime", frozen_dt))
            except AttributeError:
                pass

        for m in MIGRATIONS:
            for flag in ("0", "all"):
                os.environ["FEATURE_ASYNC_OFFLOAD"] = flag
                thread_id_box.clear()
                worker_idents.clear()
                pool_counter["n"] = 0
                response = await _call_endpoint(
                    m.consumer, bt_bt, bt_factor, bt_market, bt_risk, bt_export,
                    bt_portfolio, bt_stock)
                results[(m.consumer, flag)] = {
                    "response": _normalize_response(response),
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
    covered = {m.consumer for m in MIGRATIONS}
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
    for m in MIGRATIONS:
        off = results[(m.consumer, "0")]
        on = results[(m.consumer, "all")]
        if off["response"] != on["response"]:
            failures.append(f"switch diff {m.key}: response differs off vs on")
        if off["pool_calls"] != 0:
            failures.append(f"switch diff {m.key}: off-side used thread pool")
        if on["pool_calls"] != 1:
            failures.append(f"switch diff {m.key}: on-side did NOT use thread pool "
                            f"(count={on['pool_calls']})")
        if not on["worker_idents"] or on["worker_idents"][0] == on["main_ident"]:
            failures.append(f"switch diff {m.key}: on-side ran on the main thread")
        if any(i != off["main_ident"] for i in off["compute_idents"]):
            failures.append(f"switch diff {m.key}: off-side compute left the main thread")
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
        print(f"[INFO] migrations: {len(MIGRATIONS)}")
        for m in MIGRATIONS:
            print(f"       {m.consumer:18s} {m.key:32s} {m.old_fn} -> {m.new_fn} "
                  f"@ {m.pre_commit}:{m.source_rel}")
        for (consumer, flag), rec in sorted(results.items()):
            print(f"       {consumer:18s} flag={flag:3s} pool_calls={rec['pool_calls']} "
                  f"response_bytes={len(rec['response'])}")
        return 0

    if failures:
        print(f"[FAIL] offload switch: {len(failures)} problem(s)")
        for f in failures[:40]:
            print("      " + f)
        return 1
    print(f"[PASS] offload switch: {len(MIGRATIONS)} endpoints, "
          f"migration AST equivalent, off/on responses identical (field order kept), "
          f"thread pool off=0 on=1 with different worker thread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
