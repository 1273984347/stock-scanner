#!/usr/bin/env python
"""并发压测与前端路径冻结 —— RF-01 Phase 6 第 3 批（L10）。

计划 §三 Phase 6 验证方式要求：
  「20 并发 × 慢端点（/api/scan、/api/backtest），P95 不劣化」
  「前端路径清单（由 L7 从 static/ 源码重算，不再手抄数字）× 阻塞端点全集，新旧各跑」

本工具把这两条变成**确定性、零触网、低 CPU** 的机器判据：

  * 用 `time.sleep(BLOCK_SECONDS)` 在 mock 里模拟真实阻塞 IO。sleep 不占 CPU，
    且用户设备有「持续重负载保护性关机」约束，故刻意用短 small-batch（3 轮 × 20 并发），
    不跑真实回测、不挂满负载。
  * off 侧同步直调会占住事件循环 ⇒ 20 个请求串行完成；
    on 侧走 anyio 线程池 ⇒ 可并行。测的正是「事件循环是否被占住」。
  * 稳定性口径按坑表 P-09：报 3 轮的 max/min，不用小样本 CV。
  * 前端路径**不逐条实跑**：全量实跑会触网 / 写缓存 / 触发后台预取，
    破坏 off/on 对照的确定性（坑表 P-69）。改为「L7 扫描器重算 vs 登记清单」
    成员+数量双校验，并验证 flag 切换前后 `app.openapi()` 完全一致。
    数量不再硬编码：计划写 18 / ADR-0004 写 36 / 实测 38，手抄三次必然漂移。

用法：
  python scripts/concurrency_probe.py --check
  python scripts/concurrency_probe.py --summary
输出为纯 ASCII（Windows CI stdout 默认 cp1252/cp936）。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
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
from unittest.mock import Mock, patch
from fastapi.responses import JSONResponse

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def _resolve_repo_root(start):
    """自锚定仓库根：向上找同时含 AGENTS.md 与 lib/ 的目录，脚本可任意移动。"""
    for cand in (start, *start.parents):
        if (cand / "AGENTS.md").is_file() and (cand / "lib").is_dir():
            return cand
    raise SystemExit(f"[gate] 无法定位仓库根: 从 {start} 向上未找到 AGENTS.md + lib/")


ROOT = _resolve_repo_root(pathlib.Path(__file__).resolve().parent)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# routers.common 在导入期会启动 HS300 网络预取线程（坑表 P-69）。
os.environ["SKIP_HS300_PREFETCH"] = "1"

CONTRACTS = ROOT / "docs" / "refactor" / "contracts"
FRONTEND_FILE = CONTRACTS / "frontend-api-paths.json"
CONTRACT_FILE = CONTRACTS / "route-contract-baseline.json"


class _FakeABRequest:
    """ab_test_run 端点的 request.json() 桩（await 留在事件循环侧，模块级供 _call 使用）。"""
    async def json(self):
        return {"symbols": ["000001"], "days": 250, "num_runs": 2}

CONCURRENCY = 20
ROUNDS = 3
BLOCK_SECONDS = 0.02
P95_TOLERANCE = 1.10  # on 侧允许 10% 噪声；实测应远低于 off 侧
# 冻结时间戳，消除 factor-eval/risk/export_config 内嵌 datetime.now() 造成的
# 逐请求 shape 差异（属测试伪影，非 offload 引入）。
FIXED_DT = _dt.datetime(2026, 1, 1, 0, 0, 0)

ENDPOINTS = (
    ("backtest", "POST /api/backtest"),
    ("backtest_compare", "POST /api/backtest/compare"),
    ("walkforward", "POST /api/walkforward"),
    # Phase 6 第 4 批 Tier A 候选 Top5（同 run_blocking 机制，L10 需覆盖）
    ("factor_eval", "GET /api/factor-eval"),
    ("factor_scatter", "GET /api/factor-scatter"),
    ("kline", "GET /api/kline/{code}"),
    ("risk", "GET /api/risk"),
    ("export_config", "GET /api/export/config"),
    # Phase 6 第 5 批 Tier A 候选（blocking ≥4；dashboard 因 try 体含 await 不在本批）
    ("factor_contribution", "GET /api/factor-contribution"),
    ("market", "GET /api/market"),
    ("positions", "GET /api/positions"),
    ("risk_exposure", "GET /api/risk-exposure"),
    ("stock", "GET /api/stock/{code}"),
    # Phase 6 第 6 批 Tier A 候选（blocking ≥2 且不含 await）
    ("signals", "GET /api/signals"),
    ("exposures", "GET /api/exposures"),
    ("position", "GET /api/position/{code}"),
    ("regime_history", "GET /api/regime-history"),
    ("score", "GET /api/score/{code}"),
    ("sector_flow", "GET /api/sector-flow"),
    # Phase 6 第 7 批 Tier A 候选 1 阻塞调用端点（dashboard 除外 —— ADR-0013 转 RF-02）
    ("attribution", "GET /api/attribution"),
    ("audit", "GET /api/audit"),
    ("audit_detail", "GET /api/audit/{scan_id}"),
    ("export_trades", "GET /api/export/trades"),
    ("export_trades_excel", "GET /api/export/trades/excel"),
    ("export_pdf", "POST /api/export/pdf"),
    ("factor_ic_history", "GET /api/factor-ic-history"),
    ("factor_weights", "GET /api/factor-weights"),
    ("notifications", "GET /api/notifications"),
    ("notification_stats", "GET /api/notifications/stats"),
    ("position_distribution", "GET /api/position-distribution"),
    ("risk_correlation", "GET /api/risk/correlation"),
    ("sector_heatmap", "GET /api/sector-heatmap"),
    ("signals_accuracy_trend", "GET /api/signals/accuracy-trend"),
    ("strategy_ranking", "GET /api/strategy/ranking"),
    ("strategy_recommend", "GET /api/strategy/recommend"),
    ("trade_history", "GET /api/trade-history"),
    ("ab_test", "POST /api/ab-test/run"),
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

    # ---- Phase 6 第 4 批端点 mock（与 offload_switch_diff 同构，确定性零触网）----
    # 重计算/取数符号所在端点属 Tier A 候选；这里把「阻塞」位置用 sleep 模拟，
    # 让 20 并发下 off(阻塞事件循环) vs on(线程池) 的 P95 差异真实可测。
    class _FakeOrchFactor:
        def get_realtime_quotes(self):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return pd.DataFrame({
                "代码": [f"{i:06d}" for i in range(1, 7)],
                "成交额": [100.0 * i for i in range(1, 7)],
                "涨跌幅": [0.01 * i for i in range(1, 7)],
            })
        def get_stock_hist_multi_source(self, code, days=120, source="auto"):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return pd.DataFrame({"close": [10.0 + i for i in range(65)],
                                 "date": [f"2024-01-{i % 28 + 1:02d}"
                                          for i in range(65)]})
        def get_market_sentiment(self):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return object()
        def get_stock_score(self, code):
            # exposures 需要（第 6 批）：经 routers.risk._get_orchestrator 复用本类
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return {"factors": {"momentum": 1.2, "value": 0.8}}

    class _FakeMacro:
        state = "normal"; regime = "stable"
        position_multiplier = 1.0; reasoning = "test"

    class _FakeAlpha:
        def compute_ic(self, a, b):
            return {"ic": 0.1, "rank_ic": 0.2, "rank_ic_pvalue": 0.3}

    class _FakeRiskMgr:
        def __init__(self, *a, **k):
            pass
        def get_risk_budget(self, regime, level):
            return {"regime": regime, "level": level}
        def full_risk_assessment(self, **kwargs):
            return {"alerts": []}
        def compute_var_cvar(self, market_data, tickers, weights, total_value):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return {"var_95": -0.02, "cvar_95": -0.03}
        def compute_correlation_matrix(self, market_data, tickers):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return {"avg_correlation": 0.3, "diversification_score": 0.7,
                    "high_corr_pairs": []}

    def _fake_position(code, weight, shares, buy_price):
        """position_detail 需要完整字段；weight/shares/buy_price 同时服务 risk_exposure。"""
        return types.SimpleNamespace(
            code=code, name=f"股{code}", weight=weight, shares=shares, buy_price=buy_price,
            buy_date="2024-01-01", stop_loss=9.0, take_profit=15.0, notes="",
            atr=0.5, atr_mult=2.0, atr_stop_price=9.0, peak_price=11.0,
            trailing_stop_price=9.5, atr_take_profit_price=15.0,
        )

    class _FakePosMgr:
        # risk_exposure / position_detail 需要：非空持仓表（risk 端点不用该属性，互不影响）
        positions = {
            "000001": _fake_position("000001", 0.6, 1000.0, 10.0),
            "600519": _fake_position("600519", 0.4, 100.0, 1500.0),
        }
        def get_portfolio_summary(self, *a, **k):
            return {}
        def check_stop_loss(self, prices):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return []

    def fake_compute_all_signals(hist):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return {"factors": {"volume_ratio": 1.0, "adx": 2.0, "rsi": 3.0}}

    def fake_get_stock_hist(code, days=120, **kwargs):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        n = 65
        return pd.DataFrame({
            "date": [f"2024-01-{i % 28 + 1:02d}" for i in range(1, n + 1)],
            "open": [10.0 + (i % 5) for i in range(n)],
            "high": [11.0 + (i % 5) for i in range(n)],
            "low": [9.0 + (i % 5) for i in range(n)],
            "close": [10.5 + (i % 5) for i in range(n)],
            "volume": [1000 + i for i in range(n)],
        })

    def fake_ema(series, n):
        return pd.Series(list(series), dtype="float64")

    def fake_compute_atr(df):
        return 1.0

    def fake_get_config(preset):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return {"preset": preset, "key": "value", "nested": {"a": 1}}

    def fake_evaluate_macro_gate(sentiment):
        return _FakeMacro()

    def fake_run_stress_tests():
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return []

    def fake_risk_batch(codes, days=120, **kwargs):
        # 第 5 批 risk_exposure 需要：非空行情（risk 端点 tickers 为空，不触此 mock）
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return {c: pd.DataFrame({"date": ["2024-01-01", "2024-01-02"],
                                 "close": [10.0, 10.5]}) for c in codes}

    class _FakeTracker:
        def update_actual_returns(self):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
        def get_recent_signals(self, days=30):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return [
                {"source": "volume_ratio", "actual_return_5d": 0.05, "is_correct": True},
                {"source": "volume_ratio", "actual_return_5d": -0.02, "is_correct": False},
                {"source": "adx", "actual_return_5d": 0.03, "is_correct": True},
                {"source": "adx", "actual_return_5d": 0.0, "is_correct": False},
            ]
        def evaluate_signals(self, days=30):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return {"total": 4, "accuracy": 0.5}

    class _FakeOrchMarket:
        def get_market_sentiment(self):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return 0.5

    class _FakeQuotesOrch:
        def get_realtime_quotes(self):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return pd.DataFrame({
                "代码": ["000001", "600519"],
                "最新价": [10.0, 1500.0],
            })

    class _FakeOrchStock:
        def get_stock_hist(self, code, days=180):
            return fake_get_stock_hist(code, days=days)

    def fake_composite_verdict(hist):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return {"verdict": "buy", "signals": {"ema_above": True}, "summary": "ok"}

    class _FakeOrchScore:
        def get_stock_score(self, code):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return {"score": 88, "factors": {"momentum": 1.2}}

    class _FakeTradeLogger:
        def get_recent_trades(self, days=30):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
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

    def fake_get_scan_history(limit=20):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return [{"scan_id": 1, "ts": "2026-01-01"}, {"scan_id": 2, "ts": "2026-01-02"}][:limit]

    def fake_get_scan_detail(scan_id):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return {"scan_id": scan_id, "status": "done"} if scan_id == 1 else {}

    def fake_get_notifications(limit=1000):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return [
            {"type": "signal", "timestamp": "2026-01-01T10:00:00",
             "message": "买入 000001", "details": "x"},
            {"type": "risk", "timestamp": "2026-01-02T11:00:00",
             "message": "止损触发", "details": "y"},
        ]

    def fake_get_stock_blocks(code):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return ["电子"]

    def fake_compute_sector_scores(top_n=50):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return [types.SimpleNamespace(
            block="电子", total_score=72.5, avg_return_5d=1.2, volume_ratio=1.1,
            limit_up_count=3, leader_return=5.0, leader_code="000001",
            stock_count=20, score_return=0.8, score_volume=0.9, score_limit_up=0.7,
            score_leader=0.6)]

    class _FakeRanker:
        def get_ranking(self):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return [{"name": "momentum", "score": 88}]

    class _FakeOptimizer:
        def _default_weights(self):
            return {"momentum": 0.4, "volume": 0.3, "volatility": 0.3}
        def evaluate_weights(self, weights, symbols=None):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return {"sharpe": 1.1, "max_dd": -0.1, "win_rate": 0.55}

    class _FakeABTester:
        def run_test(self, config):
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
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
            worker_idents.append(threading.get_ident())
            time.sleep(BLOCK_SECONDS)
            return b"%PDF-1.4 fake-report"

    def fake_np_normal(*a, **k):
        return 0.01  # accuracy-trend / ic-history 的随机源（worker 自证靠 evaluate_signals）

    def fake_np_poisson(*a, **k):
        return 5

    def fake_np_uniform(*a, **k):
        # risk_correlation 的 worker 自证点（tolist 真跑、文件读极快）
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return 0.25

    def fake_index_hist(code, days=60):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        n = 26
        return pd.DataFrame({
            "date": [f"2024-01-{i % 28 + 1:02d}" for i in range(1, n + 1)],
            "close": [10.0 + (i % 5) for i in range(n)],
            "volume": [1000.0 + i for i in range(n)],
        })

    def fake_load_cache(key, **kwargs):
        worker_idents.append(threading.get_ident())
        time.sleep(BLOCK_SECONDS)
        return pd.DataFrame([
            {"name": "半导体", "score": 70},
            {"name": "医药", "score": 45},
            {"name": "银行", "score": 35},
        ])

    def noop_cache(*a, **k):
        return None

    stack.enter_context(patch("routers.factor._get_orchestrator",
                              lambda *a, **k: _FakeOrchFactor()))
    stack.enter_context(patch("routers.risk._get_orchestrator",
                              lambda *a, **k: _FakeOrchFactor()))
    stack.enter_context(patch("routers.factor._cache_set", noop_cache))
    stack.enter_context(patch("routers.risk._cache_set", noop_cache))
    # scan_balanced 非空：/api/risk 走全量评估、/api/exposures 走主路径（均确定性）。
    # exposures/risk_exposure 键必须返回 None：两端点判 `is not None`（P-74）。
    fake_scan_cache = {"portfolio": {"details": [
        {"code": "000001", "weight": 0.5}, {"code": "600519", "weight": 0.3}]}}
    stack.enter_context(patch("routers.risk._cache_get",
                              lambda key, **k: None if key in ("risk", "risk_exposure", "exposures")
                              else (fake_scan_cache if key == "scan_balanced" else None)))
    stack.enter_context(patch("routers.risk._batch_get_stock_hist", fake_risk_batch))
    stack.enter_context(patch("factors.compute_all_signals", fake_compute_all_signals))
    stack.enter_context(patch("factors.evaluate_macro_gate", fake_evaluate_macro_gate))
    stack.enter_context(patch("lib.scoring.alpha.AlphaSynthesizer", _FakeAlpha))
    stack.enter_context(patch("lib.data.data_layer.get_stock_hist", fake_get_stock_hist))
    stack.enter_context(patch("lib.scoring.technical_signals.ema", fake_ema))
    stack.enter_context(patch("backtest.utils.compute_atr", fake_compute_atr))
    stack.enter_context(patch("lib.risk.risk_manager.VolCorrRiskManager", _FakeRiskMgr))
    stack.enter_context(patch("lib.risk.risk_manager.VolCorrRiskConfig", object))
    stack.enter_context(patch("lib.risk.position_manager.PositionManager", _FakePosMgr))
    stack.enter_context(patch("routers.risk._run_stress_tests", fake_run_stress_tests))
    stack.enter_context(patch("config.get_config", fake_get_config))

    # ---- Phase 6 第 5 批端点 mock（与 offload_switch_diff 同构，确定性零触网）----
    stack.enter_context(patch("lib.scoring.signal_tracker.SignalTracker", _FakeTracker))
    stack.enter_context(patch("routers.market._get_orchestrator",
                              lambda *a, **k: _FakeOrchMarket()))
    stack.enter_context(patch("routers.market._cache_get", noop_cache))
    stack.enter_context(patch("routers.market._cache_set", noop_cache))
    stack.enter_context(patch("routers.portfolio._get_orchestrator",
                              lambda *a, **k: _FakeQuotesOrch()))
    stack.enter_context(patch("routers.stock._get_orchestrator",
                              lambda *a, **k: _FakeOrchStock()))
    stack.enter_context(patch("lib.scoring.technical_signals.calc_composite_verdict",
                              fake_composite_verdict))

    # ---- Phase 6 第 6 批端点 mock（signals / exposures / position / regime-history / score / sector-flow）----
    stack.enter_context(patch("routers.signals_audit._cache_get", noop_cache))
    stack.enter_context(patch("routers.signals_audit._cache_set", noop_cache))
    stack.enter_context(patch("routers.scoring._get_orchestrator",
                              lambda *a, **k: _FakeOrchScore()))
    stack.enter_context(patch("lib.data.data_layer.get_index_hist", fake_index_hist))
    stack.enter_context(patch("lib.monitoring.market_regime.detect_market_regime",
                              lambda window: {"regime": "bull"}))
    stack.enter_context(patch("lib.data.data_layer._load_cache", fake_load_cache))

    # ---- Phase 6 第 7 批端点 mock（18 个 1 调用端点；与 offload_switch_diff 同构）----
    stack.enter_context(patch("routers.analysis._cache_get", noop_cache))
    stack.enter_context(patch("routers.analysis._cache_set", noop_cache))
    stack.enter_context(patch("lib.monitoring.trade_logger.TradeLogger", _FakeTradeLogger))
    stack.enter_context(patch("lib.monitoring.audit_logger.get_scan_history",
                              fake_get_scan_history))
    stack.enter_context(patch("lib.monitoring.audit_logger.get_scan_detail",
                              fake_get_scan_detail))
    # create=True：模块实际没有模块级 get_notifications（预先存在的潜在 bug，
    # 端点真实运行时 AttributeError → 空兜底；见 TODO / ADR-0013）
    stack.enter_context(patch("lib.monitoring.notification_logger.get_notifications",
                              fake_get_notifications, create=True))
    stack.enter_context(patch("lib.data.data_layer.get_stock_blocks", fake_get_stock_blocks))
    stack.enter_context(patch("lib.strategy.sector_score.compute_sector_scores",
                              fake_compute_sector_scores))
    stack.enter_context(patch("lib.strategy.strategy_ranking.strategy_ranker", _FakeRanker()))
    # create=True：factor_optimizer 实际没有 FactorOptimizer 类（同上，预先存在 bug）
    stack.enter_context(patch("lib.scoring.factor_optimizer.FactorOptimizer",
                              _FakeOptimizer, create=True))
    stack.enter_context(patch("lib.strategy.ab_testing.ab_tester", _FakeABTester()))
    stack.enter_context(patch("lib.infra.pdf_report.pdf_generator", _FakePdfGenerator()))
    stack.enter_context(patch("numpy.random.normal", fake_np_normal))
    stack.enter_context(patch("numpy.random.poisson", fake_np_poisson))
    stack.enter_context(patch("numpy.random.uniform", fake_np_uniform))
    import tempfile as _tempfile
    _tf = _tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"000001": {"shares": 100}, "600519": {"shares": 10}}, _tf)
    _tf.close()
    stack.enter_context(patch("routers.risk.Path", lambda p: pathlib.Path(_tf.name)))
    # xlsx 字节级确定性（同 offload_switch_diff：zip 条目时间 + docProps modified）
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
    _excel_dt = Mock(wraps=_dt)
    _excel_dt.datetime.now.return_value = FIXED_DT
    stack.enter_context(patch("openpyxl.writer.excel.datetime", _excel_dt))
    # 保持同步迭代（否则消费 body 会经 anyio 线程池，污染 off 侧 main-thread 自证）
    stack.enter_context(patch("starlette.responses.iterate_in_threadpool",
                              lambda it: it))

    # 冻结 datetime.now，消除带时间戳端点的逐请求 shape 差异（测试伪影）。
    # datetime 是 C 扩展不可变类型，无法 patch 其 .now 属性；改用 Mock(wraps=...)
    # 钉住 .now，并 patch 各 router 命名空间自己的 datetime 名字（经 common import *）。
    frozen_dt = Mock(wraps=_dt.datetime)
    frozen_dt.now.return_value = FIXED_DT
    frozen_dt.utcnow.return_value = FIXED_DT
    for _ns in ("routers.factor", "routers.risk", "routers.export",
                "routers.signals_audit", "routers.market", "routers.common"):
        try:
            stack.enter_context(patch(_ns + ".datetime", frozen_dt))
        except AttributeError:
            pass
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
    if consumer == "walkforward":
        return await bt.run_walkforward(
            symbols="000001,600519", train_days=120,
            validation_days=30, step_days=30, _=None)
    if consumer == "factor_eval":
        from routers import factor as rf
        return await rf.factor_evaluation(n_stocks=30)
    if consumer == "factor_scatter":
        from routers import factor as rf
        return await rf.factor_scatter(x_factor="volume_ratio", y_factor="adx", n_stocks=30)
    if consumer == "kline":
        from routers import market as rm
        return await rm.kline_data(code="000001", days=120)
    if consumer == "risk":
        from routers import risk as rr
        return await rr.risk_assessment(_=None)
    if consumer == "export_config":
        from routers import export as re_
        return await re_.export_config(preset="balanced")
    if consumer == "factor_contribution":
        from routers import factor as rf
        return await rf.factor_contribution(days=30)
    if consumer == "market":
        from routers import market as rm
        return await rm.market_overview(_=None)
    if consumer == "positions":
        from routers import portfolio as rp
        return await rp.positions(_=None)
    if consumer == "risk_exposure":
        from routers import risk as rr
        return await rr.risk_exposure()
    if consumer == "stock":
        from routers import stock as rs
        return await rs.stock_detail(code="000001", _=None)
    if consumer == "signals":
        from routers import signals_audit as rsa
        return await rsa.signal_accuracy(days=30)
    if consumer == "exposures":
        from routers import risk as rr
        return await rr.factor_exposures(preset="balanced")
    if consumer == "position":
        from routers import portfolio as rp
        return await rp.position_detail(code="000001")
    if consumer == "regime_history":
        from routers import market as rm
        return await rm.regime_history(days=60)
    if consumer == "score":
        from routers import scoring as rsc
        return await rsc.stock_score(code="000001", _=None)
    if consumer == "sector_flow":
        from routers import market as rm
        return await rm.sector_flow(top_n=20)
    if consumer == "attribution":
        from routers import analysis as ra
        return await ra.return_attribution(days=30)
    if consumer == "audit":
        from routers import signals_audit as rsa
        return await rsa.audit_history(limit=20)
    if consumer == "audit_detail":
        from routers import signals_audit as rsa
        return await rsa.audit_detail(scan_id=1)
    if consumer == "export_trades":
        from routers import export as re_
        return await re_.export_trades(days=30)
    if consumer == "export_trades_excel":
        from routers import export as re_
        return await re_.export_trades_excel(days=30)
    if consumer == "export_pdf":
        from routers import export as re_
        return await re_.export_pdf()
    if consumer == "factor_ic_history":
        from routers import factor as rf
        return await rf.factor_ic_history(days=60)
    if consumer == "factor_weights":
        from routers import factor as rf
        return await rf.factor_weights(preset="balanced")
    if consumer == "notifications":
        from routers import notifications as rn
        return await rn.get_notifications()
    if consumer == "notification_stats":
        from routers import notifications as rn
        return await rn.get_notification_stats()
    if consumer == "position_distribution":
        from routers import portfolio as rp
        return await rp.position_distribution()
    if consumer == "risk_correlation":
        from routers import risk as rr
        return await rr.risk_correlation()
    if consumer == "sector_heatmap":
        from routers import market as rm
        return await rm.sector_heatmap(top_n=50)
    if consumer == "signals_accuracy_trend":
        from routers import signals_audit as rsa
        return await rsa.signals_accuracy_trend(days=90)
    if consumer == "strategy_ranking":
        from routers import strategy as rst
        return await rst.strategy_ranking()
    if consumer == "strategy_recommend":
        from routers import strategy as rst
        return await rst.strategy_recommend(symbols="000001")
    if consumer == "trade_history":
        from routers import market as rm
        return await rm.trade_history(days=30, code="", sort_by="date", limit=50)
    if consumer == "ab_test":
        from routers import strategy as rst
        return await rst.ab_test_run(request=_FakeABRequest())
    raise ValueError(f"unknown consumer {consumer}")


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
        # export_config 返回 JSONResponse；export_trades/excel/pdf 返回 StreamingResponse
        # （iterate_in_threadpool 已 patch 成恒等，body_iterator 是同步迭代器）；
        # 统一剥成可比对 payload
        if isinstance(resp, JSONResponse):
            payload = json.loads(resp.body.decode())
        elif hasattr(resp, "body_iterator"):
            chunks = []
            it = resp.body_iterator
            while True:
                try:
                    chunks.append(next(it))
                except StopIteration:
                    break
            body = b"".join(c if isinstance(c, bytes) else str(c).encode("utf-8")
                            for c in chunks)
            payload = body.decode("utf-8", errors="replace")
        else:
            payload = resp
        assert payload, "empty/corrupt response"
        shapes.add(json.dumps(payload, sort_keys=True, default=str))
        return elapsed

    durations = await asyncio.gather(*[one() for _ in range(CONCURRENCY)])
    idents = set(worker_idents)
    return (list(durations), idents == {main_ident},
            any(i != main_ident for i in idents), shapes)


def _load_frontend_scanner():
    """按项目惯例用 importlib 加载 L7 的前端扫描器（同 _load_contract_tool 的做法）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_route_ownership_for_l10", ROOT / "scripts" / "route_ownership_check.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.capture_frontend_paths


def _frontend_freeze() -> tuple[list[str], dict]:
    """前端路径冻结：登记清单必须等于从 static/ 源码重扫的独立集合 + off/on OpenAPI 冻结。

    根因（批次 B）：旧实现把「36」硬编码在探针里，而清单由 L7 从源码 capture 生成，
    计划写 18、ADR-0004 写 36、实测 38 —— 同一个数字被手抄三次必然漂移。
    改为「重算 vs 登记」后数量自动跟随；偷偷增删清单成员仍会判红，
    不会退化成「引用清单自身 counts」那种自己校验自己的空判据（坑表 P-54）。
    """
    fe = json.loads(FRONTEND_FILE.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    declared = {r["path"] for r in contract["routes"] if r.get("category") == "api"}
    paths = fe["normalized_paths"]
    registered = set(paths)
    failures = []

    live = _load_frontend_scanner()()
    live_paths = set(live["normalized_paths"])
    if registered != live_paths:
        failures.append(
            "frontend paths drift vs source scan: "
            f"missing={sorted(live_paths - registered)} added={sorted(registered - live_paths)}"
        )
    # counts 是清单自带的登记读数，必须与实际成员数自洽（防止只改 counts 不改清单）
    if fe["counts"]["normalized_paths"] != len(paths):
        failures.append(
            f"frontend counts.normalized_paths={fe['counts']['normalized_paths']} "
            f"!= len(normalized_paths)={len(paths)}"
        )
    if live["counts"]["unmapped"] != 0:
        failures.append(f"source scan unmapped={live['counts']['unmapped']}")
    if not registered <= declared:
        failures.append(f"unmapped frontend paths: {sorted(registered - declared)}")
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
    return failures, {"paths": len(paths), "scanned": len(live_paths),
                      "declared": len(declared), "openapi_frozen": off == on}


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
