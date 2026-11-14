"""RF-01 Phase 5 —— api.py 拆分的共享层。

原 api.py 的全部 import、模块级状态、非路由 helper 集中在此；
各域 router 通过 `from .common import *` 复用，末尾 `__all__` 显式导出下划线 helper。
"""
import pathlib
# api.py 原在仓库根，STATIC_DIR/_perf_file/hs300 缓存均按根定位；
# 本模块移至 routers/ 后必须以仓库根为锚，否则静态/缓存落盘目录漂移。
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

"""FastAPI 后端 —— 量化交易系统 API

端点清单：
- GET /              前端页面
- GET /api/health    系统健康探针（无需认证）
- GET /api/scan      选股+风控+组合优化
- GET /api/score/{code}  单股四维评分
- GET /api/stock/{code}  单股因子详情
- GET /api/market    市场概况+风控
- GET /api/positions 持仓概况
- GET /api/position-distribution 持仓分布（按行业分组）
- GET /api/risk      完整风控评估
- GET /api/exposures 因子暴露追踪
- GET /api/dashboard 仪表盘汇总数据
- GET /api/signals   信号准确率追踪
- GET /api/audit     审计日志
- GET /api/audit/{id} 审计详情
- POST /api/backtest 回测
- POST /api/backtest/compare 多策略对比回测
- GET /api/factor-contribution 因子贡献度分析
- GET /api/kline/{code}  K线数据（OHLCV）
- GET /api/trade-history 交易历史（支持按时间/股票/盈亏筛选排序）
- GET /api/attribution 收益归因分解（按信号来源瀑布图）
- GET /api/settings  用户设置
- POST /api/settings 保存用户设置
"""


from fastapi import FastAPI, HTTPException, Query, Security, Request


from fastapi.staticfiles import StaticFiles


from fastapi.responses import FileResponse, JSONResponse, StreamingResponse


from fastapi.security import APIKeyHeader


from pathlib import Path


from datetime import datetime


from typing import Optional


import csv


import io


import json


import logging


import re


import threading


import os


import asyncio


import secrets


import time


try:
    import psutil
    _has_psutil = True
except ImportError:
    _has_psutil = False


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


logger = logging.getLogger(__name__)


from lib.infra.api_core import (
    API_KEY_HEADER,
    CACHE_DIR,
    _cache_get,
    _cache_lock,
    _cache_set,
    _get_api_key,
    _validate_stock_code,
    verify_api_key,
    verify_ws_token,
)


from lib.infra.api_core import add_security_headers as _add_security_headers


from lib.infra.api_core import http_exception_handler as _http_exception_handler_impl


_start_time = time.time()


from lib.infra.scan_task_registry import ALLOWED_PRESETS


from lib.infra.perf_metrics import (
    _perf_metrics,
    record_metric as _record_metric_impl,
    flush_metrics as _flush_metrics_impl,
)


_perf_file = PROJECT_ROOT / "cache" / "perf_baseline.json"


def _record_metric(path: str, duration: float, status: int):
    """记录性能指标 —— Phase 6：收口到 lib.infra.perf_metrics 的带锁实现。

    薄壳，字段名 / 数值 / 触发阈值与改造前完全一致（api.py 中间件仍调本函数）。
    """
    _record_metric_impl({
        "path": path,
        "duration_ms": round(duration * 1000, 1),
        "status": status,
        "timestamp": datetime.now().isoformat(),
    }, flush_path=_perf_file)


def _flush_metrics():
    """将指标写入文件 —— 薄壳转发，真正的锁与原子性在 lib.infra 里。"""
    _flush_metrics_impl(_perf_file)


from lib.data.cache import CircuitBreaker


circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)


def _prefetch_hs300():
    """后台预获取沪深300数据（首次启动时缓存）"""
    try:
        import akshare as ak
        import pandas as pd
        logger.info("后台预获取沪深300数据...")
        idx = ak.stock_zh_index_daily(symbol="sh000300")
        if not idx.empty:
            data = {
                "close": idx["close"].tail(120).tolist(),
                "volume": idx["volume"].tail(120).tolist() if "volume" in idx.columns else [],
                "pct_chg": idx["close"].pct_change().fillna(0).tail(120).tolist(),
            }
            _cache_set("hs300", data)
            df = idx.tail(120).copy()
            try:
                df.to_parquet(PROJECT_ROOT / "cache" / "hs300_cache.parquet", index=False)
            except Exception:
                pass
            logger.info("沪深300数据缓存成功")
    except Exception as e:
        logger.warning(f"预获取沪深300失败（不影响使用）: {e}")


if os.environ.get("SKIP_HS300_PREFETCH", "").strip().lower() not in ("1", "true", "yes"):
    threading.Thread(target=_prefetch_hs300, daemon=True).start()


STATIC_DIR = PROJECT_ROOT / "static"


from scanner import ScanOrchestrator


from lib.data.broker import LRUCache


_orchestrator_cache = LRUCache(maxsize=16)


_backtest_semaphore = asyncio.Semaphore(2)


_wf_semaphore = asyncio.Semaphore(2)


from concurrent.futures import ThreadPoolExecutor


def _batch_get_stock_hist(codes: list, days: int = 120, source: str = "auto") -> dict:
    """并行批量获取多只股票历史数据，避免 N+1 串行查询"""
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_get_orchestrator().get_stock_hist_multi_source, code, days, source): code
            for code in codes
        }
        for future in futures:
            code = futures[future]
            try:
                hist = future.result()
                if hist is not None and not hist.empty:
                    results[code] = hist
            except Exception:
                continue
    return results


def _get_orchestrator(preset: str = "balanced") -> ScanOrchestrator:
    """获取指定 preset 的编排器（带缓存，线程安全，LRU 自动淘汰）"""
    orch = _orchestrator_cache.get(preset)
    if orch is None:
        orch = ScanOrchestrator(preset=preset)
        _orchestrator_cache.set(preset, orch)
    return orch


import threading


from lib.infra.scan_task_registry import (
    _scan_tasks,  # 兼容旧引用；新代码禁止直接读写，一律走下列带锁 API
    start_task,
    finish_task,
    fail_task,
    get_task,
)


def _run_scan_background(preset: str):
    """后台线程执行扫描"""
    cache_key = f"scan_{preset}"
    start_task(preset)
    try:
        from lib.data.data_layer import get_realtime_quotes
        get_realtime_quotes()

        orchestrator = _get_orchestrator(preset)
        scan_result = circuit_breaker.call("scan", orchestrator.run_all_strategies)

        if not scan_result:
            scan_result = {}
        results = scan_result.get("results", {})
        output = {}
        for name, df in results.items():
            if hasattr(df, 'to_dict'):
                output[name] = df.to_dict(orient="records")
            else:
                output[name] = []

        data = {
            "preset": preset,
            "results": output,
            "total": sum(len(v) for v in output.values()),
            "risk_check": scan_result.get("risk_check", {}),
            "portfolio": scan_result.get("portfolio", {}),
            "factor_health": scan_result.get("factor_health", {}),
            "macro_filter": scan_result.get("macro_filter", {}),
            "index_signal": scan_result.get("index_signal", {}),
        }
        _cache_set(cache_key, data)
        finish_task(preset)
        logger.info(f"[Scan] {preset} 完成: {data['total']} 只候选股")
    except Exception as e:
        fail_task(preset, str(e))
        logger.error(f"[Scan] {preset} 失败: {e}")


def _extract_equity_curve(result: dict) -> list:
    """从回测结果中提取净值曲线数据"""
    import pandas as pd
    perf_df = result.get("perf_df")
    equity_curve = []
    if perf_df is not None and hasattr(perf_df, 'empty') and not perf_df.empty:
        if "total_value" in perf_df.columns:
            tv = perf_df["total_value"].astype(float)
            base = tv.iloc[0] if tv.iloc[0] > 0 else 1.0
            nav_series = tv / base
        else:
            nav_series = pd.Series([1.0] * len(perf_df))

        for i, (_, row) in enumerate(perf_df.iterrows()):
            equity_curve.append({
                "date": str(row.get("trade_date", row.get("date", row.name))),
                "nav": round(float(nav_series.iloc[i]), 4),
                "benchmark": 1.0,
            })
    return equity_curve


def _run_stress_tests() -> dict:
    """压力测试：相关系数冲击 + 波动率冲击 + 行业暴跌"""
    scenarios = {}

    # 场景1：相关性冲击（所有相关系数→0.9）
    scenarios["correlation_shock"] = {
        "description": "所有股票相关系数飙升至0.9（类似2008、2015恐慌）",
        "impact": "组合波动率预计上升40-60%，分散化效果消失",
        "action": "建议减仓至风险预算的50%",
    }

    # 场景2：波动率翻倍
    scenarios["volatility_doubles"] = {
        "description": "市场波动率翻倍（类似2020年3月）",
        "impact": "ATR止损将被频繁触发，持仓周期缩短50%",
        "action": "仓位缩减至正常水平的70%",
    }

    # 场景3：行业暴跌
    scenarios["sector_crash"] = {
        "description": "重仓行业单日暴跌15%",
        "impact": "取决于行业集中度，组合可能回撤5-10%",
        "action": "确保单行业暴露不超过30%",
    }

    return scenarios


SETTINGS_FILE = PROJECT_ROOT / "cache" / "user_settings.json"


def _default_settings() -> dict:
    """默认用户设置"""
    return {
        "default_preset": "balanced",
        "default_days": 250,
        "default_symbols": "",
        "notifications": {
            "signals": True,
            "trades": True,
            "alerts": True,
            "daily_report": True,
        },
        "theme": "dark",
        "language": "zh",
    }


async def sector_flow(top_n: int = Query(20, ge=5, le=50)):
    """板块资金流向 — 桑基图数据"""
    try:
        from lib.strategy.sector_rotation import SectorMomentum
        sm = SectorMomentum()
        # 获取板块数据
        from lib.data.data_layer import _load_cache
        import numpy as np
        sectors_data = _load_cache("sectors/sector_scores", max_age_hours=24)
        if sectors_data is None or (hasattr(sectors_data, 'empty') and sectors_data.empty):
            return {"nodes": [], "links": []}
        # 构建桑基图数据
        nodes = []
        links = []
        if hasattr(sectors_data, 'iterrows'):
            for _, row in sectors_data.head(top_n).iterrows():
                name = row.get("name", row.get("sector", ""))
                score = row.get("score", 50)
                if name:
                    nodes.append({"name": str(name)})
                    # 资金流向：从低分板块流向高分板块
                    if score > 60:
                        links.append({"source": "资金池", "target": str(name), "value": int(score)})
                    elif score < 40:
                        links.append({"source": str(name), "target": "资金池", "value": int(100 - score)})
        if nodes:
            nodes.insert(0, {"name": "资金池"})
        return {"nodes": nodes[:top_n + 1], "links": links[:top_n * 2]}
    except Exception as e:
        logger.error(f"Sector flow error: {e}")
        return {"nodes": [], "links": []}


from fastapi import WebSocket, WebSocketDisconnect


import asyncio


MAX_WS_CONNECTIONS = 100  # 最大 WebSocket 连接数


class ConnectionManager:
    """B2-1：连接表由锁保护，广播在快照上执行，避免与 connect/disconnect 竞态。"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._connections_lock = threading.Lock()

    async def connect(self, websocket: WebSocket, subprotocol: str | None = None):
        with self._connections_lock:
            if len(self.active_connections) >= MAX_WS_CONNECTIONS:
                over = True
            else:
                over = False
                # 先占位再 accept：并发连接不会集体越过上限
                self.active_connections.append(websocket)
        if over:
            await websocket.close(code=1013, reason="Too many connections")
            logger.warning(f"WebSocket 连接数超限 ({MAX_WS_CONNECTIONS})，拒绝连接")
            return False
        try:
            # 批次 C / P1-2：回显客户端提供的子协议，浏览器才接受握手。
            await websocket.accept(subprotocol=subprotocol)
        except Exception:
            # accept 失败时不能把占位留在连接表里，否则会泄漏名额
            self.disconnect(websocket)
            raise
        return True

    def disconnect(self, websocket: WebSocket):
        with self._connections_lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, payload: dict) -> None:
        """把同一份数据推给所有连接；推送失败的连接就地摘除。"""
        with self._connections_lock:
            targets = list(self.active_connections)
        stale = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        if stale:
            with self._connections_lock:
                for ws in stale:
                    if ws in self.active_connections:
                        self.active_connections.remove(ws)


manager = ConnectionManager()

# 批次 B2-1：唯一的定时构造任务。旧实现每个连接各自 while+sleep(10)+构造，
# 100 连接 ≈ 10 次/秒全市场拉取，全压在事件循环上；现在全局 1 次/10 秒。
WS_BROADCAST_INTERVAL_S = 10


async def _ws_broadcast_loop():
    """单后台任务：构造一次，广播给所有连接（P1-3）。"""
    while True:
        await asyncio.sleep(WS_BROADCAST_INTERVAL_S)
        try:
            data = await _get_ws_data()
            await manager.broadcast({
                "type": "data_update",
                "timestamp": datetime.now().isoformat(),
                "data": data,
            })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"WebSocket broadcast error: {e}")


async def _get_ws_data():
    """获取 WebSocket 推送数据（增强版：含持仓盈亏、板块评分、信号追踪）"""
    try:
        # 只从缓存读取，不触发任何计算
        # 与 dashboard_cache_key("balanced") 同键；无 preset 上下文，读默认视图。
        dashboard = _cache_get("dashboard", max_age=600) or {}
        scan = _cache_get("scan_balanced", max_age=3600) or {}
        risk = _cache_get("risk", max_age=600) or {}
        exposures = _cache_get("exposures", max_age=3600) or {}

        # 持仓盈亏（从 PositionManager 读取）
        position_pnl = {}
        try:
            from lib.risk.position_manager import PositionManager
            pm = PositionManager()
            if pm.positions:
                # 获取实时行情
                orchestrator = _get_orchestrator()
                quotes = orchestrator.get_realtime_quotes()
                prices = {}
                if not quotes.empty:
                    for _, row in quotes.iterrows():
                        code = str(row.get("代码", ""))
                        price = row.get("最新价")
                        if code and price:
                            prices[code] = float(price)

                for code, pos in pm.positions.items():
                    current = prices.get(code, 0)
                    if pos.buy_price > 0 and current > 0:
                        pnl_pct = (current - pos.buy_price) / pos.buy_price
                        position_pnl[code] = {
                            "cost": pos.buy_price,
                            "current": current,
                            "pnl_pct": round(pnl_pct, 4),
                            "shares": pos.shares,
                        }
        except Exception:
            pass

        # 板块评分（从缓存读取）
        sector_heatmap = _cache_get("sector_heatmap", max_age=1800) or {}

        # 信号追踪
        signals = _cache_get("signals", max_age=1800) or {}

        return {
            "dashboard": dashboard,
            "scan": scan,
            "risk": risk,
            "exposures": exposures,
            "position_pnl": position_pnl,
            "sector_heatmap": sector_heatmap.get("sectors", [])[:10],
            "signal_summary": signals.get("summary", {}),
        }
    except Exception as e:
        logger.error(f"WebSocket data fetch error: {e}")
        return {"dashboard": {}, "scan": {}, "risk": {}, "exposures": {}}


def dashboard_cache_key(preset: str = "balanced") -> str:
    """仪表盘缓存键（批次 A / P1-4）：必须纳入全部影响结果的参数。

    旧实现固定 "dashboard"，先请求 preset=A 后 10 分钟 TTL 内任意 preset=B
    会直接返回 A 的组合/候选/计数，且不再触发 B 的计算。键生成收口到此处，
    避免字符串散落导致读写两侧再次漂移。
    """
    # 默认 preset 沿用历史键 "dashboard"：export_pdf / WS 等无 preset 上下文的
    # 消费者读的就是这个键，保持它们零改动（也避免已有缓存整体失效）。
    # 只隔离非默认 preset，此时才需要把影响结果的参数纳入键。
    if preset == "balanced":
        return "dashboard"
    return f"dashboard_{preset}"


ALERT_RULES_FILE = Path("alert_rules.json")


def _load_alert_rules():
    """加载告警规则"""
    if ALERT_RULES_FILE.exists():
        try:
            with open(ALERT_RULES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def _save_alert_rules(rules):
    """保存告警规则"""
    with open(ALERT_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)



# 显式导出：含下划线 helper，使 `from .common import *` 在域模块中可用
__all__ = [k for k in globals() if not k.startswith("__")]
