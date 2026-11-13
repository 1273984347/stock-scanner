#!/usr/bin/env python
"""Offload 开关前后响应 diff + 迁移等价性 —— RF-01 Phase 6 取证（T4）。

Phase 6 承诺「只改调度方式，不改可观测结果」。本工具把这句话变成三层机器判据：

  1. **迁移等价**：每条迁移记录改造前源码（固定 commit）中 `try:` 块语句序列，
     与现文件 `_compute_*` 同步函数体逐语句 `ast.dump` 对比。任何「顺手优化」都会
     立刻判红（ADR-0003 D5）。每条迁移自带 `source_rel` + `pre_commit`，
     提交之后仍可复算（backtest 固定 523b353；第 4 批 77bc366；第 5 批 6d0e434；
     第 6 批 497550f；第 7 批 c9b4d6f）。
     **有意分歧豁免**：RF-02 D1/D2 对 `notifications` / `strategy_recommend` 两个迁移的
     `_compute_*` 体做了**有意 bug 修复**（ADR-0015），已记入 `MIGRATION_DIVERGENCES`；
     门禁对它们仅做「接线完整性」校验（`_compute_*` 函数存在），豁免逐字等价比对。
     其余迁移仍严格冻结（任何未登记的体改动立刻判红）。
     搬迁锚点按形态分三类（lead_strip + body_mode）：默认 try 体；无 try 端点
     （如 /api/market）剥函数体前 N 条快守卫；try 体含不可搬迁前导语句的端点
     （如 ab-test 的 `await request.json()`）剥 try 体前 N 条。
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
# lead_strip + body_mode：搬迁锚点。
#   body_mode="try"（默认）：旧端点最外层 try 块为搬迁体；lead_strip 再剥掉
#     try 体前 N 条不可搬迁语句（如 ab_test 的 `body = await request.json()`，
#     请求体解析必须留在事件循环侧，ADR-0012 D5 同源）。
#   body_mode="full"：端点无 try 块，函数体剥 docstring 后再剥前 N 条快守卫
#     （缓存命中分支 / `_validate_stock_code` 校验）即为搬迁体。
Migration = namedtuple("Migration",
                       "consumer key old_fn new_fn source_rel pre_commit lead_strip body_mode",
                       defaults=(0, "try"))

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
              "routers/market.py", "6d0e434", 2, "full"),
    Migration("positions", "GET /api/positions", "positions", "_compute_positions",
              "routers/portfolio.py", "6d0e434"),
    Migration("risk_exposure", "GET /api/risk-exposure", "risk_exposure", "_compute_risk_exposure",
              "routers/risk.py", "6d0e434"),
    Migration("stock", "GET /api/stock/{code}", "stock_detail", "_compute_stock_detail",
              "routers/stock.py", "6d0e434", 1, "full"),
    # Phase 6 第 6 批 Tier A 候选（基线 commit 497550f = batch6 起点，均未 offload；
    # /api/dashboard try 体含 await，不在本批）
    Migration("signals", "GET /api/signals", "signal_accuracy", "_compute_signal_accuracy",
              "routers/signals_audit.py", "497550f"),
    Migration("exposures", "GET /api/exposures", "factor_exposures", "_compute_factor_exposures",
              "routers/risk.py", "497550f"),
    Migration("position", "GET /api/position/{code}", "position_detail", "_compute_position_detail",
              "routers/portfolio.py", "497550f"),
    Migration("regime_history", "GET /api/regime-history", "regime_history", "_compute_regime_history",
              "routers/market.py", "497550f"),
    Migration("score", "GET /api/score/{code}", "stock_score", "_compute_stock_score",
              "routers/scoring.py", "497550f"),
    Migration("sector_flow", "GET /api/sector-flow", "sector_flow", "_compute_sector_flow",
              "routers/market.py", "497550f"),
    # Phase 6 第 7 批 Tier A 候选 1 阻塞调用端点（基线 commit c9b4d6f = batch7 起点，
    # 均未 offload）；/api/dashboard 转 RF-02（ADR-0013），不在本批
    Migration("attribution", "GET /api/attribution", "return_attribution",
              "_compute_return_attribution", "routers/analysis.py", "c9b4d6f"),
    Migration("audit", "GET /api/audit", "audit_history",
              "_compute_audit_history", "routers/signals_audit.py", "c9b4d6f"),
    Migration("audit_detail", "GET /api/audit/{scan_id}", "audit_detail",
              "_compute_audit_detail", "routers/signals_audit.py", "c9b4d6f"),
    Migration("export_trades", "GET /api/export/trades", "export_trades",
              "_compute_export_trades", "routers/export.py", "c9b4d6f"),
    Migration("export_trades_excel", "GET /api/export/trades/excel", "export_trades_excel",
              "_compute_export_trades_excel", "routers/export.py", "c9b4d6f"),
    Migration("export_pdf", "POST /api/export/pdf", "export_pdf",
              "_compute_export_pdf", "routers/export.py", "c9b4d6f"),
    Migration("factor_ic_history", "GET /api/factor-ic-history", "factor_ic_history",
              "_compute_factor_ic_history", "routers/factor.py", "c9b4d6f"),
    Migration("factor_weights", "GET /api/factor-weights", "factor_weights",
              "_compute_factor_weights", "routers/factor.py", "c9b4d6f"),
    Migration("notifications", "GET /api/notifications", "get_notifications",
              "_compute_notifications", "routers/notifications.py", "c9b4d6f"),
    Migration("notification_stats", "GET /api/notifications/stats", "get_notification_stats",
              "_compute_notification_stats", "routers/notifications.py", "c9b4d6f"),
    Migration("position_distribution", "GET /api/position-distribution", "position_distribution",
              "_compute_position_distribution", "routers/portfolio.py", "c9b4d6f"),
    Migration("risk_correlation", "GET /api/risk/correlation", "risk_correlation",
              "_compute_risk_correlation", "routers/risk.py", "c9b4d6f"),
    Migration("sector_heatmap", "GET /api/sector-heatmap", "sector_heatmap",
              "_compute_sector_heatmap", "routers/market.py", "c9b4d6f"),
    Migration("signals_accuracy_trend", "GET /api/signals/accuracy-trend", "signals_accuracy_trend",
              "_compute_signals_accuracy_trend", "routers/signals_audit.py", "c9b4d6f"),
    Migration("strategy_ranking", "GET /api/strategy/ranking", "strategy_ranking",
              "_compute_strategy_ranking", "routers/strategy.py", "c9b4d6f"),
    Migration("strategy_recommend", "GET /api/strategy/recommend", "strategy_recommend",
              "_compute_strategy_recommend", "routers/strategy.py", "c9b4d6f"),
    Migration("trade_history", "GET /api/trade-history", "trade_history",
              "_compute_trade_history", "routers/market.py", "c9b4d6f"),
    Migration("ab_test", "POST /api/ab-test/run", "ab_test_run",
              "_compute_ab_test_run", "routers/strategy.py", "c9b4d6f", 1),
)

# RF-02 (ADR-0015 D1/D2) **有意分歧**：以下两个迁移的 `_compute_*` 体在 offload 之后被做了
# bug 修复，不再与 offload 前的端点体逐字等价（这是修复本身，不是「顺手优化」）。
# 门禁对它们降级为「接线完整性」校验（`_compute_*` 函数仍存在、offload 接线未断），
# 严格的逐字等价比对被豁免；行为正确性由 tests/features/test_rf02_bad_endpoints.py 覆盖。
# 其余迁移继续严格冻结（任何未在此清单中的体改动仍会立刻判红）。
# 新增有意分歧必须同步更新本清单 + ADR-0015 + tests/features/test_rf02_bad_endpoints.py。
MIGRATION_DIVERGENCES: dict[str, str] = {
    "notifications": "RF-02 D1: 关键词检索 message/details → title/body（ADR-0015）",
    "strategy_recommend": "RF-02 D2: FactorOptimizer 幻影类 → eval_current_weights（ADR-0015）",
}

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
    if m.body_mode == "full":
        return _strip_leading_imports(_body_after_guard(fn, m.lead_strip))
    body = _old_try_body(fn)
    if m.lead_strip:
        body = list(body)[m.lead_strip:]
    return _strip_leading_imports(body)


def check_migration() -> list[str]:
    failures = []
    for m in MIGRATIONS:
        if m.consumer in MIGRATION_DIVERGENCES:
            # RF-02 (ADR-0015 D1/D2) 有意分歧：_compute_* 体已偏离 offload 前端点体
            # （bug 修复），不再要求逐字等价。仅校验 _compute_* 函数仍存在（接线未断）；
            # 行为正确性由 tests/features/test_rf02_bad_endpoints.py 覆盖。
            try:
                new_tree = ast.parse((ROOT / m.source_rel).read_text(encoding="utf-8"))
                _find_fn(new_tree, m.new_fn)
            except (OSError, LookupError) as e:
                failures.append(f"migration {m.consumer}: diverged compute fn missing: {e}")
            continue
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
    def get_stock_score(self, code):
        # exposures 需要：非空因子评分（risk 端点不用该方法，互不影响）
        return {"factors": {"momentum": 1.2, "value": 0.8}}


class _FakeOrchScore:
    def get_stock_score(self, code):
        return {"score": 88, "factors": {"momentum": 1.2}}


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
    def full_risk_assessment(self, **kwargs):
        # scan_balanced 缓存非空时 /api/risk 走全量评估路径（第 6 批起）
        return {
            "volatility": {"regime": "normal"},
            "risk_budget": {"equity": 0.65},
            "var": {"var_95": -0.02},
            "correlation": {"avg_correlation": 0.3},
            "alerts": [types.SimpleNamespace(rule_name="position_limit",
                                             level="low", message="ok")],
        }


def _fake_position(code, weight, shares, buy_price):
    """position_detail 需要完整字段；weight/shares/buy_price 同时服务 risk_exposure。"""
    return types.SimpleNamespace(
        code=code, name=f"股{code}", weight=weight, shares=shares, buy_price=buy_price,
        buy_date="2024-01-01", stop_loss=9.0, take_profit=15.0, notes="",
        atr=0.5, atr_mult=2.0, atr_stop_price=9.0, peak_price=11.0,
        trailing_stop_price=9.5, atr_take_profit_price=15.0,
    )


class _FakePositionMgr:
    # risk_exposure / position_detail 需要：非空持仓表（risk 端点不用该属性，互不影响）
    positions = {
        "000001": _fake_position("000001", 0.6, 1000.0, 10.0),
        "600519": _fake_position("600519", 0.4, 100.0, 1500.0),
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
            {"source": "volume_ratio", "actual_return_5d": 0.05, "is_correct": True},
            {"source": "volume_ratio", "actual_return_5d": -0.02, "is_correct": False},
            {"source": "adx", "actual_return_5d": 0.03, "is_correct": True},
            {"source": "adx", "actual_return_5d": 0.0, "is_correct": False},
        ]
    def evaluate_signals(self, days=30):
        return {"total": 4, "accuracy": 0.5}


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


class _FakeTradeLogger:
    def get_recent_trades(self, days=30):
        return [
            {"timestamp": "2026-01-01 10:00:00", "code": "000001", "action": "BUY",
             "price": 10.0, "shares": 100, "pnl_pct": 0.0, "hold_days": 0,
             "exit_reason": "", "signal_source": "momentum"},
            {"timestamp": "2026-01-02 11:00:00", "code": "000001", "action": "SELL",
             "price": 10.5, "shares": 100, "pnl_pct": 0.05, "hold_days": 1,
             "exit_reason": "take_profit", "signal_source": "momentum"},
            {"timestamp": "2026-01-03 12:00:00", "code": "600519", "action": "SELL",
             "price": 1500.0, "shares": 10, "pnl_pct": -0.02, "hold_days": 2,
             "exit_reason": "stop_loss", "source": "adx"},
        ]


class _FakeRanker:
    def get_ranking(self):
        return [{"name": "momentum", "score": 88}]


class _FakeOptimizer:
    def _default_weights(self):
        return {"momentum": 0.4, "volume": 0.3, "volatility": 0.3}
    def evaluate_weights(self, weights, symbols=None):
        return {"sharpe": 1.1, "max_dd": -0.1, "win_rate": 0.55}


class _FakeABTester:
    def run_test(self, config):
        return types.SimpleNamespace(
            group_a_name="默认", group_b_name="优化",
            a_sharpe=1.0, a_return=0.1, a_max_drawdown=-0.05, a_win_rate=0.5, a_trades=10,
            b_sharpe=1.2, b_return=0.12, b_max_drawdown=-0.06, b_win_rate=0.55, b_trades=12,
            sharpe_p_value=0.2, return_p_value=0.3, sharpe_diff=0.2, return_diff=0.02,
            sharpe_ci_lower=0.1, sharpe_ci_upper=0.3,
            return_ci_lower=0.01, return_ci_upper=0.03,
            winner="B", confidence="low", recommendation="继续观察")


class _FakePdfGenerator:
    def generate(self, data):
        return b"%PDF-1.4 fake-report"


class _FakeABRequest:
    """ab_test_run 端点的 request.json() 桩（await 留在事件循环侧）。"""
    async def json(self):
        return {"symbols": ["000001"], "days": 250, "num_runs": 2}


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
    # scan_balanced 非空：/api/risk 走全量评估、/api/exposures 走主路径（均确定性）。
    # exposures/risk_exposure 键必须返回 None：两端点判 `is not None`（P-74），{} 会让
    # 缓存命中分支直接返回、根本不进 run_blocking（线程池自证必红）。
    fake_scan_cache = {"portfolio": {"details": [
        {"code": "000001", "weight": 0.5}, {"code": "600519", "weight": 0.3}]}}
    stack.enter_context(patch("routers.risk._cache_get",
                              lambda key, **k: None if key in ("risk", "risk_exposure", "exposures")
                              else (fake_scan_cache if key == "scan_balanced" else {})))
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

    # ---- Phase 6 第 6 批（signals / exposures / position / regime-history / score / sector-flow）----
    stack.enter_context(patch("routers.signals_audit._cache_get", lambda *a, **k: None))
    stack.enter_context(patch("routers.signals_audit._cache_set", lambda *a, **k: None))
    stack.enter_context(patch("routers.scoring._get_orchestrator",
                              lambda *a, **k: _FakeOrchScore()))
    def fake_index_hist(code, days=60):
        n = 26
        return pd.DataFrame({
            "date": [f"2024-01-{i % 28 + 1:02d}" for i in range(1, n + 1)],
            "close": [10.0 + (i % 5) for i in range(n)],
            "volume": [1000.0 + i for i in range(n)],
        })
    stack.enter_context(patch("lib.data.data_layer.get_index_hist", fake_index_hist))
    stack.enter_context(patch("lib.monitoring.market_regime.detect_market_regime",
                              lambda window: {"regime": "bull"}))
    def fake_load_cache(key, **kwargs):
        return pd.DataFrame([
            {"name": "半导体", "score": 70},
            {"name": "医药", "score": 45},
            {"name": "银行", "score": 35},
        ])
    stack.enter_context(patch("lib.data.data_layer._load_cache", fake_load_cache))

    # ---- Phase 6 第 7 批（18 个 1 调用端点）----
    stack.enter_context(patch("routers.analysis._cache_get", lambda *a, **k: None))
    stack.enter_context(patch("routers.analysis._cache_set", lambda *a, **k: None))
    stack.enter_context(patch("lib.monitoring.trade_logger.TradeLogger", _FakeTradeLogger))
    def fake_get_scan_history(limit=20):
        return [{"scan_id": 1, "ts": "2026-01-01"}, {"scan_id": 2, "ts": "2026-01-02"}][:limit]
    stack.enter_context(patch("lib.monitoring.audit_logger.get_scan_history",
                              fake_get_scan_history))
    def fake_get_scan_detail(scan_id):
        return {"scan_id": scan_id, "status": "done"} if scan_id == 1 else {}
    stack.enter_context(patch("lib.monitoring.audit_logger.get_scan_detail",
                              fake_get_scan_detail))
    def fake_get_notifications(limit=1000):
        return [
            {"type": "signal", "timestamp": "2026-01-01T10:00:00",
             "message": "买入 000001", "details": "x"},
            {"type": "risk", "timestamp": "2026-01-02T11:00:00",
             "message": "止损触发", "details": "y"},
        ]
    # create=True：模块实际没有模块级 get_notifications（端点在真实运行时
    # AttributeError → 空兜底，属预先存在的潜在 bug，本批逐字搬迁不改行为、
    # 单独登记 TODO；mock 注入让搬迁体的真实筛选/分页逻辑可被取证）
    stack.enter_context(patch("lib.monitoring.notification_logger.get_notifications",
                              fake_get_notifications, create=True))
    stack.enter_context(patch("lib.data.data_layer.get_stock_blocks",
                              lambda code: ["电子"]))
    def fake_compute_sector_scores(top_n=50):
        return [types.SimpleNamespace(
            block="电子", total_score=72.5, avg_return_5d=1.2, volume_ratio=1.1,
            limit_up_count=3, leader_return=5.0, leader_code="000001",
            stock_count=20, score_return=0.8, score_volume=0.9, score_limit_up=0.7,
            score_leader=0.6)]
    stack.enter_context(patch("lib.strategy.sector_score.compute_sector_scores",
                              fake_compute_sector_scores))
    stack.enter_context(patch("lib.strategy.strategy_ranking.strategy_ranker", _FakeRanker()))
    # create=True：factor_optimizer 实际没有 FactorOptimizer 类（GeneticOptimizer /
    # run_optimization）—— /api/strategy/recommend 真实运行时 ImportError → 空兜底，
    # 与 notifications 同款预先存在的潜在 bug（本批逐字搬迁不改行为，单独登记 TODO）
    stack.enter_context(patch("lib.scoring.factor_optimizer.FactorOptimizer",
                              _FakeOptimizer, create=True))
    stack.enter_context(patch("lib.strategy.ab_testing.ab_tester", _FakeABTester()))
    stack.enter_context(patch("lib.infra.pdf_report.pdf_generator", _FakePdfGenerator()))

    # np.random 钉死：accuracy-trend / ic-history / risk-correlation 的随机源
    # （off/on 两侧各自消耗随机数序列，不钉死则响应必然分叉）
    stack.enter_context(patch("numpy.random.normal", lambda *a, **k: 0.01))
    stack.enter_context(patch("numpy.random.poisson", lambda *a, **k: 5))
    stack.enter_context(patch("numpy.random.uniform", lambda *a, **k: 0.25))

    # risk_correlation 的持仓文件：临时确定性 JSON（Path 在 routers.risk 命名空间）
    import tempfile as _tempfile
    _tf = _tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"000001": {"shares": 100}, "600519": {"shares": 10}}, _tf)
    _tf.close()
    stack.enter_context(patch("routers.risk.Path", lambda p: pathlib.Path(_tf.name)))

    # xlsx 字节级确定性：zip 条目时间戳（time.localtime）+ openpyxl save 强制写的
    # docProps modified（openpyxl.writer.excel.datetime）+ DocumentProperties 默认值
    import openpyxl as _openpyxl
    _real_workbook = _openpyxl.Workbook
    def _fixed_workbook(*a, **k):
        wb = _real_workbook(*a, **k)
        wb.properties.created = FIXED_DT
        wb.properties.modified = FIXED_DT
        return wb
    stack.enter_context(patch("time.localtime",
                              lambda *a: (2026, 1, 1, 0, 0, 0, 0, 1, 0)))
    stack.enter_context(patch("openpyxl.Workbook", _fixed_workbook))
    _excel_dt = Mock(wraps=_dt)  # excel 模块持有的是 datetime 模块（datetime.datetime.now(...)）
    _excel_dt.datetime.now.return_value = FIXED_DT
    stack.enter_context(patch("openpyxl.writer.excel.datetime", _excel_dt))

    # StreamingResponse 默认把同步迭代器包成 anyio 线程池异步迭代（starlette ≥0.15），
    # 消费 body 会污染 off 侧 pool_calls 自证（off 必须 ==0）——取证语境下保持同步迭代
    stack.enter_context(patch("starlette.responses.iterate_in_threadpool",
                              lambda it: it))

    return thread_id_box


def _normalize_response(obj) -> str:
    """JSONResponse 取 `.body` 字节；StreamingResponse 消费 body_iterator 并连同
    media_type / status / headers 一起比对（body 本身已在 mock 下确定性，
    xlsx 的时间戳由 time.localtime + openpyxl.writer.excel.datetime 冻结）；
    普通 dict/list 直接序列化。off/on 一致比对用。"""
    if hasattr(obj, "body_iterator"):
        chunks = []
        it = obj.body_iterator
        while True:
            try:
                chunks.append(next(it))
            except StopIteration:
                break
        body = b"".join(c if isinstance(c, bytes) else str(c).encode("utf-8")
                        for c in chunks)
        return (f"{obj.media_type}|{obj.status_code}|{sorted(obj.raw_headers)}|"
                + body.decode("utf-8", errors="replace"))
    if hasattr(obj, "body"):
        return obj.body.decode("utf-8")
    return json.dumps(obj, ensure_ascii=False, sort_keys=False, default=str)


async def _call_endpoint(consumer, bt_bt, bt_factor, bt_market, bt_risk, bt_export,
                         bt_portfolio, bt_stock, bt_signals, bt_scoring,
                         bt_analysis, bt_notif, bt_strategy):
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
    if consumer == "signals":
        return await bt_signals.signal_accuracy(days=30)
    if consumer == "exposures":
        return await bt_risk.factor_exposures(preset="balanced")
    if consumer == "position":
        return await bt_portfolio.position_detail(code="000001")
    if consumer == "regime_history":
        return await bt_market.regime_history(days=60)
    if consumer == "score":
        return await bt_scoring.stock_score(code="000001", _=None)
    if consumer == "sector_flow":
        return await bt_market.sector_flow(top_n=20)
    if consumer == "attribution":
        return await bt_analysis.return_attribution(days=30)
    if consumer == "audit":
        return await bt_signals.audit_history(limit=20)
    if consumer == "audit_detail":
        return await bt_signals.audit_detail(scan_id=1)
    if consumer == "export_trades":
        return await bt_export.export_trades(days=30)
    if consumer == "export_trades_excel":
        return await bt_export.export_trades_excel(days=30)
    if consumer == "export_pdf":
        return await bt_export.export_pdf()
    if consumer == "factor_ic_history":
        return await bt_factor.factor_ic_history(days=60)
    if consumer == "factor_weights":
        return await bt_factor.factor_weights(preset="balanced")
    if consumer == "notifications":
        return await bt_notif.get_notifications()
    if consumer == "notification_stats":
        return await bt_notif.get_notification_stats()
    if consumer == "position_distribution":
        return await bt_portfolio.position_distribution()
    if consumer == "risk_correlation":
        return await bt_risk.risk_correlation()
    if consumer == "sector_heatmap":
        return await bt_market.sector_heatmap(top_n=50)
    if consumer == "signals_accuracy_trend":
        return await bt_signals.signals_accuracy_trend(days=90)
    if consumer == "strategy_ranking":
        return await bt_strategy.strategy_ranking()
    if consumer == "strategy_recommend":
        return await bt_strategy.strategy_recommend(symbols="000001")
    if consumer == "trade_history":
        return await bt_market.trade_history(days=30, code="", sort_by="date", limit=50)
    if consumer == "ab_test":
        return await bt_strategy.ab_test_run(request=_FakeABRequest())
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
    from routers import signals_audit as bt_signals
    from routers import scoring as bt_scoring
    from routers import analysis as bt_analysis
    from routers import notifications as bt_notif
    from routers import strategy as bt_strategy
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
                    "routers.signals_audit", "routers.market", "routers.common"):
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
                    bt_portfolio, bt_stock, bt_signals, bt_scoring,
                    bt_analysis, bt_notif, bt_strategy)
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
        print(f"[INFO] migrations: {len(MIGRATIONS)} "
              f"({len(MIGRATION_DIVERGENCES)} intentionally diverged per RF-02/ADR-0015)")
        for m in MIGRATIONS:
            mark = " [diverged]" if m.consumer in MIGRATION_DIVERGENCES else ""
            print(f"       {m.consumer:18s} {m.key:32s} {m.old_fn} -> {m.new_fn} "
                  f"@ {m.pre_commit}:{m.source_rel}{mark}")
        for (consumer, flag), rec in sorted(results.items()):
            print(f"       {consumer:18s} flag={flag:3s} pool_calls={rec['pool_calls']} "
                  f"response_bytes={len(rec['response'])}")
        return 0

    if failures:
        print(f"[FAIL] offload switch: {len(failures)} problem(s)")
        for f in failures[:40]:
            print("      " + f)
        return 1
    diverged = len(MIGRATION_DIVERGENCES)
    strict = len(MIGRATIONS) - diverged
    print(f"[PASS] offload switch: {strict} endpoints migration-AST-equivalent "
          f"+ {diverged} intentionally diverged (RF-02/ADR-0015, wiring-only check); "
          f"off/on responses identical (field order kept), "
          f"thread pool off=0 on=1 with different worker thread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
