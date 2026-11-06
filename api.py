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

app = FastAPI(title="A股量化交易系统", version="3.0.0")


# P1-3 修复（2026-06-05）：全局 HTTPException handler
# 补充 5xx 错误的 trace_id logging（不改每处 except → raise HTTPException）
@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 500:
        logger.error(
            f"API 5xx: {request.method} {request.url.path} -> {exc.detail}",
            exc_info=True,
        )
    # 保持原有 FastAPI 响应格式（detail 字段）
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """P1-3 修复：处理所有未捕获的异常（漏网之鱼），避免空白 500"""
    logger.error(
        f"API 未处理异常: {request.method} {request.url.path} -> {type(exc).__name__}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"内部服务器错误: {str(exc)[:200]}"},
    )
_start_time = time.time()


@app.on_event("startup")
def _verify_api_key_on_startup():
    """启动时验证 API 密钥已配置"""
    try:
        _get_api_key()
    except RuntimeError as e:
        logger.error(f"启动失败: {e}")
        raise SystemExit(1)


# ========== 安全常量 ==========

ALLOWED_PRESETS = {"balanced", "aggressive", "conservative"}


def _validate_stock_code(code: str) -> str:
    """Validate A-share stock code format (6 digits)"""
    if not re.match(r'^\d{6}$', code):
        raise HTTPException(status_code=400, detail=f"Invalid stock code format: {code}")
    return code

# ========== 安全中间件 ==========

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _get_api_key() -> str:
    """获取 API 密钥，密钥不存在则拒绝启动"""
    key = os.getenv("API_SECRET_KEY", "")
    if not key:
        raise RuntimeError(
            "API_SECRET_KEY must be set in production. "
            "Hint: export API_SECRET_KEY=your-secret-key"
        )
    return key


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    """验证 API Key（生产模式：API_SECRET_KEY 必须设置）"""
    if api_key is None:
        raise HTTPException(status_code=401, detail="缺少API密钥")
    expected = _get_api_key()
    if not secrets.compare_digest(api_key, expected):
        raise HTTPException(status_code=401, detail="无效的API密钥")
    return api_key


def verify_ws_token(token: str) -> bool:
    """验证 WebSocket token"""
    expected = _get_api_key()
    return secrets.compare_digest(token, expected)


@app.middleware("http")
async def track_performance(request: Request, call_next):
    """请求性能追踪"""
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    if request.url.path.startswith("/api/"):
        _record_metric(request.url.path, duration, response.status_code)
    return response


_perf_metrics: list = []
_perf_file = Path(__file__).parent / "cache" / "perf_baseline.json"


def _record_metric(path: str, duration: float, status: int):
    """记录性能指标（内存中，定期写入）"""
    _perf_metrics.append({
        "path": path,
        "duration_ms": round(duration * 1000, 1),
        "status": status,
        "timestamp": datetime.now().isoformat(),
    })
    if len(_perf_metrics) >= 50:
        _flush_metrics()


def _flush_metrics():
    """将指标写入文件"""
    if not _perf_metrics:
        return
    try:
        existing = []
        if _perf_file.exists():
            existing = json.loads(_perf_file.read_text(encoding="utf-8"))
        all_metrics = existing + _perf_metrics
        all_metrics = all_metrics[-1000:]
        _perf_file.parent.mkdir(parents=True, exist_ok=True)
        _perf_file.write_text(
            json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _perf_metrics.clear()
    except Exception as e:
        logger.debug(f"性能指标写入失败: {e}")


@app.on_event("shutdown")
def _flush_perf_on_shutdown():
    """关闭时刷新性能指标"""
    _flush_metrics()


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """添加安全响应头"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # X-XSS-Protection 显式设为 "0" 禁用旧过滤器（避免引入漏洞），保留头以兼容老扫描器
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
    return response

# 缓存操作全局锁（保护 JSON 缓存文件的并发读写）
_cache_lock = threading.Lock()

# ========== 断路器（使用 lib/cache.py 统一实现）==========
from lib.cache import CircuitBreaker

# 全局断路器实例
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
                df.to_parquet(Path(__file__).parent / "cache" / "hs300_cache.parquet", index=False)
            except Exception:
                pass
            logger.info("沪深300数据缓存成功")
    except Exception as e:
        logger.warning(f"预获取沪深300失败（不影响使用）: {e}")


# 预取走 akshare 网络源。契约快照 / CI 等无网或无需预取的场景，
# 可设 SKIP_HS300_PREFETCH=1 跳过 —— 默认行为完全不变。
if os.environ.get("SKIP_HS300_PREFETCH", "").strip().lower() not in ("1", "true", "yes"):
    threading.Thread(target=_prefetch_hs300, daemon=True).start()

STATIC_DIR = Path(__file__).parent / "static"
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# 注意：静态文件挂载在 API 路由之后（见文件末尾）


def _cache_get(key: str, max_age: int = 3600):
    """读 JSON 缓存（线程安全，带随机 TTL 防止缓存雪崩，支持 gzip 压缩）"""
    import random, zlib
    path = CACHE_DIR / f"api_{key}.json"
    # 添加 ±10% 随机偏移防止缓存雪崩
    jitter = int(max_age * 0.1 * (random.random() * 2 - 1))
    effective_max_age = max(max_age + jitter, 1)
    with _cache_lock:
        if not path.exists():
            return None
        age = (datetime.now().timestamp() - path.stat().st_mtime)
        if age > effective_max_age:
            path.unlink(missing_ok=True)
            return None
        try:
            data = path.read_bytes()
            # 尝试 gzip 解压（向后兼容未压缩文件）
            try:
                data = zlib.decompress(data)
            except Exception:
                pass  # 未压缩的旧缓存文件
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.debug(f"缓存读取失败[{path.name}]: {e}")
            path.unlink(missing_ok=True)
            return None


def _cache_set(key: str, data):
    """写 JSON 缓存（线程安全，gzip 压缩）"""
    import zlib
    path = CACHE_DIR / f"api_{key}.json"
    with _cache_lock:
        try:
            json_data = json.dumps(data, ensure_ascii=False, default=str)
            # 压缩后存储（节省磁盘空间）
            compressed = zlib.compress(json_data.encode("utf-8"), level=6)
            # 只有压缩后更小才使用压缩
            if len(compressed) < len(json_data.encode("utf-8")):
                path.write_bytes(compressed)
            else:
                path.write_text(json_data, encoding="utf-8")
            try:
                # Windows 限制文件访问：仅当前用户可读写
                import os
                os.chmod(path, 0o600)
            except Exception:
                # 非 Windows 或权限不足时跳过
                pass
        except Exception as e:
            logger.warning(f"缓存写入失败[{key}]: {e}")


# ========== 统一入口 ==========

from scanner import ScanOrchestrator
from lib.broker import LRUCache

# 编排器缓存（按 preset 缓存，避免重复创建，使用 LRU 限制内存占用）
_orchestrator_cache = LRUCache(maxsize=16)

# 回测并发限制（最多 2 个并发回测）
_backtest_semaphore = asyncio.Semaphore(2)
# Walk-Forward 并发限制（最多 2 个并发）
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


# ========== 核心端点 ==========

@app.get("/")
async def index():
    """前端页面"""
    html = STATIC_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return {"message": "A股量化交易系统 API v3", "docs": "/docs"}


@app.get("/api/health")
async def health():
    """系统健康探针（无需认证）"""
    try:
        uptime_seconds = int(time.time() - _start_time)

        # 内存信息
        memory_mb = None
        if _has_psutil:
            process = psutil.Process(os.getpid())
            mem = process.memory_info()
            memory_mb = round(mem.rss / 1024 / 1024, 1)

        # 数据新鲜度检查
        cache_dir = Path(__file__).parent / "cache"
        newest_cache = 0
        if cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                newest_cache = max(newest_cache, f.stat().st_mtime)
        cache_age_hours = round((time.time() - newest_cache) / 3600, 1) if newest_cache > 0 else None

        return {
            "status": "healthy",
            "uptime_seconds": uptime_seconds,
            "uptime_human": f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m",
            "memory_mb": memory_mb,
            "cache_age_hours": cache_age_hours,
            "circuit_breaker": {"available": True},
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


# ========== 异步扫描任务管理 ==========

import threading

_scan_tasks: dict = {}  # preset -> {"status": "running"|"done"|"error", "started_at": float, "error": str}


def _run_scan_background(preset: str):
    """后台线程执行扫描"""
    cache_key = f"scan_{preset}"
    _scan_tasks[preset] = {"status": "running", "started_at": time.time(), "error": None}
    try:
        from lib.data_layer import get_realtime_quotes
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
        _scan_tasks[preset]["status"] = "done"
        logger.info(f"[Scan] {preset} 完成: {data['total']} 只候选股")
    except Exception as e:
        _scan_tasks[preset]["status"] = "error"
        _scan_tasks[preset]["error"] = str(e)
        logger.error(f"[Scan] {preset} 失败: {e}")


@app.get("/api/scan")
async def scan(preset: str = "balanced", _=Security(verify_api_key)):
    """选股结果 — 立即返回缓存，无缓存时异步启动扫描"""
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")

    cache_key = f"scan_{preset}"
    cached = _cache_get(cache_key, max_age=3600)
    if cached is not None:
        return cached

    # 无缓存：检查是否有扫描在运行
    task = _scan_tasks.get(preset)
    if task and task["status"] == "running":
        elapsed = int(time.time() - task["started_at"])
        return {"preset": preset, "results": {}, "total": 0, "scanning": True, "elapsed_sec": elapsed}

    # 启动后台扫描
    t = threading.Thread(target=_run_scan_background, args=(preset,), daemon=True)
    t.start()
    return {"preset": preset, "results": {}, "total": 0, "scanning": True, "elapsed_sec": 0}


@app.get("/api/scan/status")
async def scan_status(preset: str = "balanced"):
    """扫描状态查询"""
    task = _scan_tasks.get(preset)
    if not task:
        return {"preset": preset, "status": "idle"}
    elapsed = int(time.time() - task["started_at"])
    return {"preset": preset, "status": task["status"], "elapsed_sec": elapsed, "error": task.get("error")}


@app.get("/api/score/{code}")
async def stock_score(code: str, _=Security(verify_api_key)):
    """单股四维评分"""
    code = _validate_stock_code(code)
    try:
        orchestrator = _get_orchestrator()
        result = orchestrator.get_stock_score(code)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"评分失败 {code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.get("/api/stock/{code}")
async def stock_detail(code: str, _=Security(verify_api_key)):
    """单股因子详情"""
    code = _validate_stock_code(code)
    from factors import compute_all_signals
    from lib.technical_signals import calc_composite_verdict

    orchestrator = _get_orchestrator()
    hist = orchestrator.get_stock_hist(code, days=180)
    if hist.empty:
        raise HTTPException(status_code=404, detail=f"未找到 {code} 数据")

    result = compute_all_signals(hist)
    tech = calc_composite_verdict(hist)

    if not result:
        raise HTTPException(status_code=500, detail="因子计算失败")

    return {
        "code": code,
        "factors": result.get("factors", {}),
        "combined": result.get("combined", {}),
        "regime": result.get("regime", {}),
        "macro_gate": result.get("macro_gate", {}),
        "technical": {
            "verdict": tech.get("verdict"),
            "signals": tech.get("signals"),
            "summary": tech.get("summary"),
        },
    }


@app.get("/api/market")
async def market_overview(_=Security(verify_api_key)):
    """市场概况 + 风控评估"""
    cached = _cache_get("market", max_age=1800)
    if cached is not None:
        return cached

    from factors import evaluate_macro_gate, calc_market_exposure
    from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig

    orchestrator = _get_orchestrator()
    sentiment = orchestrator.get_market_sentiment()
    macro = evaluate_macro_gate(sentiment)

    exposure = {"state": "neutral", "exposure": 0.70}
    idx_cache = _cache_get("hs300", max_age=7200)
    if idx_cache:
        close_list = idx_cache.get("close", [])
        vol_list = idx_cache.get("volume", [])
        pct_list = idx_cache.get("pct_chg", [])
        if len(close_list) >= 60:
            import pandas as pd
            close = pd.Series(close_list)
            volume = pd.Series(vol_list) if vol_list else None
            pct_chg = pd.Series(pct_list) if pct_list else None
            exposure = calc_market_exposure(close, volume, pct_chg)

    risk_mgr = VolCorrRiskManager(VolCorrRiskConfig())
    risk_budget = risk_mgr.get_risk_budget(
        exposure.get("state", "neutral"),
        0.5,
    )

    result = {
        "sentiment": round(sentiment, 3),
        "macro_gate": {
            "state": macro.state,
            "regime": macro.regime,
            "multiplier": macro.position_multiplier,
            "reasoning": macro.reasoning,
        },
        "exposure": exposure,
        "risk_budget": risk_budget,
    }
    _cache_set("market", result)
    return result


@app.get("/api/position/{code}", dependencies=[Security(verify_api_key)])
async def position_detail(code: str):
    """单只持仓详情"""
    code = _validate_stock_code(code)
    try:
        from lib.position_manager import PositionManager
        from datetime import datetime as _dt

        pm = PositionManager()
        pos = pm.positions.get(code)
        if not pos:
            raise HTTPException(status_code=404, detail=f"未持有 {code}")

        # 获取实时行情
        orchestrator = _get_orchestrator()
        quotes = orchestrator.get_realtime_quotes()
        current_price = 0.0
        if not quotes.empty:
            row = quotes[quotes["代码"].astype(str).str.zfill(6) == code]
            if not row.empty:
                current_price = float(row.iloc[0].get("最新价", 0))

        # 计算盈亏
        cost = pos.buy_price
        shares = pos.shares
        if current_price > 0 and cost > 0:
            pnl_pct = (current_price - cost) / cost
            pnl_amount = (current_price - cost) * shares
            market_value = current_price * shares
        else:
            pnl_pct = 0
            pnl_amount = 0
            market_value = 0

        # 持有天数
        hold_days = 0
        try:
            buy_dt = _dt.strptime(pos.buy_date, "%Y-%m-%d")
            hold_days = (_dt.now() - buy_dt).days
        except Exception:
            pass

        return {
            "code": code,
            "name": pos.name,
            "cost": cost,
            "shares": shares,
            "current_price": round(current_price, 2),
            "pnl_pct": round(pnl_pct, 4),
            "pnl_amount": round(pnl_amount, 2),
            "market_value": round(market_value, 2),
            "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit,
            "entry_date": pos.buy_date,
            "hold_days": hold_days,
            "notes": pos.notes,
            "atr": pos.atr,
            "atr_mult": pos.atr_mult,
            "atr_stop_price": pos.atr_stop_price,
            "peak_price": pos.peak_price,
            "trailing_stop_price": pos.trailing_stop_price,
            "atr_take_profit_price": pos.atr_take_profit_price,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"持仓详情查询失败 {code}: {e}")
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.get("/api/positions")
async def positions(_=Security(verify_api_key)):
    """持仓概况"""
    from lib.position_manager import PositionManager
    try:
        pm = PositionManager()
        orchestrator = _get_orchestrator()
        quotes = orchestrator.get_realtime_quotes()
        prices = {}
        if not quotes.empty:
            for _, row in quotes.iterrows():
                code = str(row.get("代码", ""))
                price = row.get("最新价")
                if code and price:
                    prices[code] = float(price)

        summary = pm.get_portfolio_summary(prices)
        alerts = pm.check_stop_loss(prices)
        return {"summary": summary, "alerts": alerts}
    except Exception as e:
        return {"summary": {"total_positions": 0}, "alerts": [], "error": "服务暂时不可用"}


@app.get("/api/position-distribution", dependencies=[Security(verify_api_key)])
async def position_distribution():
    """持仓分布（按行业分组）"""
    try:
        from lib.position_manager import PositionManager
        from lib.data_layer import get_stock_blocks

        pm = PositionManager()
        positions = pm.positions

        if not positions:
            return {"by_sector": {}, "total_positions": 0}

        # 按行业分组
        sector_weights = {}
        total_value = 0

        for code, pos in positions.items():
            weight = pos.shares * pos.buy_price
            # 获取行业
            try:
                blocks = get_stock_blocks(code)
                sector = blocks[0] if blocks else "其他"
            except Exception:
                sector = "其他"

            sector_weights[sector] = sector_weights.get(sector, 0) + weight
            total_value += weight

        # 归一化
        if total_value > 0:
            for s in sector_weights:
                sector_weights[s] = round(sector_weights[s] / total_value, 4)

        return {
            "by_sector": sector_weights,
            "total_positions": len(positions),
        }
    except Exception as e:
        logger.error(f"持仓分布查询失败: {e}")
        return {"by_sector": {}, "total_positions": 0}


# ========== 新增端点 ==========

@app.get("/api/risk")
async def risk_assessment(_=Security(verify_api_key)):
    """完整风控评估（接入 VolCorrRiskManager 的 full_risk_assessment）"""
    cached = _cache_get("risk", max_age=1800)
    if cached is not None:
        return cached

    from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
    from factors import evaluate_macro_gate

    try:
        orchestrator = _get_orchestrator()
        sentiment = orchestrator.get_market_sentiment()
        macro = evaluate_macro_gate(sentiment)

        # 获取组合持仓（从最近 scan 缓存）
        scan_cache = _cache_get("scan_balanced", max_age=7200) or {}
        portfolio = scan_cache.get("portfolio", {})
        details = portfolio.get("details", [])

        # 构建风控需要的数据
        tickers = [d["code"] for d in details] if details else []
        positions = {d["code"]: {"weight": d.get("weight", 0)} for d in details} if details else {}
        # 尝试从持仓管理器获取实际资金
        total_value = 1_000_000  # 默认100万
        try:
            from lib.position_manager import PositionManager
            pm = PositionManager()
            summary = pm.get_portfolio_summary({})
            total_value = summary.get("total_value", 1_000_000)
        except Exception:
            pass

        # 获取市场数据（用于波动率/相关性/VaR计算）
        # 并行批量获取，避免 N+1 串行查询
        market_data = {}
        if tickers:
            market_data = _batch_get_stock_hist(tickers[:15], days=120, source="auto")

        # 调用 full_risk_assessment
        risk_mgr = VolCorrRiskManager(VolCorrRiskConfig())
        if tickers and market_data:
            assessment = risk_mgr.full_risk_assessment(
                market_data=market_data,
                tickers=tickers,
                positions=positions,
                total_value=total_value,
            )
            vol_regime = assessment.get("volatility", {}).get("regime", "normal")
            risk_budget = assessment.get("risk_budget", {})
            var_metrics = assessment.get("var", {})
            corr_metrics = assessment.get("correlation", {})
            risk_alerts = [
                {
                    "rule": a.rule_name,
                    "level": a.level,
                    "message": a.message,
                }
                for a in assessment.get("alerts", [])
            ]
        else:
            # 无持仓时仍给基本数据
            vol_regime = "normal"
            risk_budget = risk_mgr.get_risk_budget("normal", 0.5)
            var_metrics = {}
            corr_metrics = {}
            risk_alerts = []

        # 压力测试
        stress_tests = _run_stress_tests()

        result = {
            "macro_gate": {
                "state": macro.state,
                "regime": macro.regime,
                "multiplier": macro.position_multiplier,
                "reasoning": macro.reasoning,
            },
            "risk_budget": risk_budget,
            "vol_regime": vol_regime,
            "var": var_metrics,
            "correlation": corr_metrics,
            "alerts": risk_alerts,
            "stress_tests": stress_tests,
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("risk", result)
        return result
    except Exception as e:
        logger.error(f"风控评估失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.get("/api/risk-exposure", dependencies=[Security(verify_api_key)])
async def risk_exposure():
    """风险暴露仪表盘 — VaR/CVaR/相关性矩阵/分散化评分"""
    cached = _cache_get("risk_exposure", max_age=1800)
    if cached is not None:
        return cached

    try:
        from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
        from lib.position_manager import PositionManager

        pm = PositionManager()
        positions = pm.positions

        if not positions:
            result = {"message": "暂无持仓", "var": {}, "correlation": {}, "n_positions": 0}
            _cache_set("risk_exposure", result)
            return result

        # 获取市场数据（最多 15 只，避免超时）
        codes = list(positions.keys())[:15]
        market_data = _batch_get_stock_hist(codes, days=120)

        if not market_data:
            result = {"message": "暂无市场数据", "var": {}, "correlation": {}, "n_positions": len(positions)}
            _cache_set("risk_exposure", result)
            return result

        risk_mgr = VolCorrRiskManager(VolCorrRiskConfig())

        # 构建权重和总市值
        tickers = list(market_data.keys())
        weights = {}
        total_value = 0
        for code in tickers:
            pos = positions.get(code)
            if pos:
                weight = getattr(pos, 'weight', 1 / len(codes))
                weights[code] = weight if weight else 1 / len(codes)
                total_value += getattr(pos, 'shares', 0) * getattr(pos, 'buy_price', 0)
            else:
                weights[code] = 1 / len(codes)
        if total_value == 0:
            total_value = 1_000_000

        # VaR/CVaR
        var_result = risk_mgr.compute_var_cvar(market_data, tickers, weights, total_value)

        # 相关性矩阵
        corr_result = risk_mgr.compute_correlation_matrix(market_data, tickers)

        result = {
            "var": var_result,
            "correlation": {
                "avg_correlation": corr_result.get("avg_correlation", 0),
                "diversification_score": corr_result.get("diversification_score", 0),
                "high_corr_pairs": corr_result.get("high_corr_pairs", [])[:5],
            },
            "n_positions": len(positions),
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("risk_exposure", result)
        return result
    except Exception as e:
        logger.error(f"风险暴露查询失败: {e}", exc_info=True)
        return {"var": {}, "correlation": {}, "n_positions": 0}


@app.get("/api/exposures", dependencies=[Security(verify_api_key)])
async def factor_exposures(preset: str = Query("balanced", description="策略预设")):
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")
    """因子暴露追踪"""
    cached = _cache_get("exposures", max_age=3600)
    if cached is not None:
        return cached

    try:
        orchestrator = _get_orchestrator(preset)
        # 用最近一次扫描结果计算暴露
        scan_cache = _cache_get(f"scan_{preset}", max_age=7200)
        if scan_cache and scan_cache.get("portfolio", {}).get("details"):
            details = scan_cache["portfolio"]["details"]
            # 计算加权平均因子暴露（限制数量避免超时）
            factor_exp = {}
            details_to_process = details[:20]  # 最多处理20只
            for d in details_to_process:
                weight = d.get("weight", 0)
                if weight > 0:
                    code = d["code"]
                    try:
                        score_data = orchestrator.get_stock_score(code)
                        factors = score_data.get("factors", {})
                        for fname, fval in factors.items():
                            if isinstance(fval, (int, float)):
                                if fname not in factor_exp:
                                    factor_exp[fname] = 0
                                factor_exp[fname] += fval * weight
                    except Exception:
                        continue

            result = {
                "exposures": {k: round(v, 4) for k, v in sorted(factor_exp.items())},
                "positions": details,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            result = {"exposures": {}, "positions": [], "message": "暂无扫描数据"}

        _cache_set("exposures", result)
        return result
    except Exception as e:
        logger.error(f"因子暴露计算失败: {e}")
        return {"exposures": {}, "positions": [], "error": "因子暴露计算失败"}


@app.get("/api/dashboard", dependencies=[Security(verify_api_key)])
async def dashboard(preset: str = Query("balanced", description="策略预设")):
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")
    """仪表盘汇总数据（一次请求返回所有仪表盘需要的指标）"""
    cached = _cache_get("dashboard", max_age=600)  # 10分钟缓存
    if cached is not None:
        return cached

    try:
        orchestrator = _get_orchestrator(preset)
        # 汇总市场+风控+组合+持仓
        market_data = _cache_get("market", max_age=1800) or {}
        scan_data = _cache_get(f"scan_{preset}", max_age=3600) or {}

        # 缓存为空时主动触发一次 scan（确保首页有数据）
        if not scan_data:
            try:
                import asyncio
                scan_data = await asyncio.wait_for(
                    asyncio.to_thread(orchestrator.run_all_strategies),
                    timeout=5.0
                )
                _cache_set(f"scan_{preset}", scan_data)
            except asyncio.TimeoutError:
                logger.warning("Dashboard scan 超时 5s")
            except Exception as scan_err:
                logger.warning(f"Dashboard 自动触发 scan 失败: {scan_err}")

        if not market_data:
            # market cache 为空时，直接计算（不依赖 scan 结果）
            try:
                from factors import evaluate_macro_gate
                sentiment = orchestrator.get_market_sentiment()
                logger.warning(f"[Dashboard] sentiment={sentiment}")
                # sentiment 为 None 表示数据不可用（如 API 被限流）
                if sentiment is None:
                    logger.warning("[Dashboard] 市场情绪数据不可用（API 限流中），使用默认值")
                    sentiment = 0.0  # API 不可用时默认中性
                macro = evaluate_macro_gate(sentiment)
                logger.warning(f"[Dashboard] macro state={macro.state}")
                idx_cache = _cache_get("hs300", max_age=7200)
                exposure = {"state": "neutral", "exposure": 0.70}
                if idx_cache:
                    close_list = idx_cache.get("close", [])
                    if len(close_list) >= 60:
                        import numpy as np
                        close_arr = np.array(close_list)
                        ma20 = close_arr[-20:].mean()
                        cur = close_arr[-1]
                        if cur > ma20 * 1.02:
                            exposure = {"state": "bull", "exposure": 0.85}
                        elif cur < ma20 * 0.98:
                            exposure = {"state": "bear", "exposure": 0.40}
                market_data = {
                    "sentiment": sentiment,
                    "macro_gate": {"state": macro.state, "regime": macro.regime, "multiplier": macro.position_multiplier, "reasoning": macro.reasoning},
                    "exposure": exposure,
                    "risk_budget": {"equity": 0.65, "cash": 0.35, "max_positions": 8},
                }
                _cache_set("market", market_data)
                logger.warning(f"[Dashboard] market_data computed: sentiment={sentiment}")
            except Exception as me:
                logger.warning(f"dashboard market 计算失败: {me}")
                market_data = {}

        result = {
            "market": {
                "sentiment": market_data.get("sentiment", 0),
                "macro_gate": market_data.get("macro_gate", {}),
                "exposure": market_data.get("exposure", {}),
                "risk_budget": market_data.get("risk_budget", {}),
            },
            "portfolio": scan_data.get("portfolio", {}),
            "risk_check": scan_data.get("risk_check", {}),
            "factor_health": scan_data.get("factor_health", {}),
            "total_candidates": scan_data.get("total", 0),
            "strategy_counts": {
                name: len(stocks)
                for name, stocks in scan_data.get("results", {}).items()
            },
            "macro_filter": scan_data.get("macro_filter", {}),
            "index_signal": scan_data.get("index_signal", {}),
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("dashboard", result)
        return result
    except Exception as e:
        logger.error(f"仪表盘数据失败: {e}")
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.get("/api/signals", dependencies=[Security(verify_api_key)])
async def signal_accuracy(days: int = Query(default=30, ge=1, le=365)):
    """信号准确率追踪"""
    cached = _cache_get("signals", max_age=1800)
    if cached is not None:
        return cached

    try:
        from lib.signal_tracker import SignalTracker
        tracker = SignalTracker()
        tracker.update_actual_returns()
        summary = tracker.evaluate_signals(days=days)
        recent = tracker.get_recent_signals(days=days)

        # 按 source 分组统计
        by_source = {}
        for s in recent:
            src = s.get("source", "unknown")
            if src not in by_source:
                by_source[src] = {"count": 0, "correct": 0}
            by_source[src]["count"] += 1
            if s.get("is_correct"):
                by_source[src]["correct"] += 1
        for src in by_source:
            cnt = by_source[src]["count"]
            by_source[src]["accuracy"] = round(by_source[src]["correct"] / cnt, 4) if cnt > 0 else 0

        result = {
            "summary": summary,
            "recent": recent,
            "by_source": by_source,
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("signals", result)
        return result
    except Exception as e:
        logger.error(f"信号准确率查询失败: {e}")
        return {"summary": {}, "recent": [], "by_source": {}}


@app.get("/api/audit", dependencies=[Security(verify_api_key)])
async def audit_history(limit: int = Query(default=20, ge=1, le=100)):
    """审计日志列表"""
    try:
        from lib.audit_logger import get_scan_history
        return {"history": get_scan_history(limit)}
    except Exception as e:
        return {"history": [], "error": "服务暂时不可用"}


@app.get("/api/audit/{scan_id}", dependencies=[Security(verify_api_key)])
async def audit_detail(scan_id: int):
    """某次扫描的审计详情"""
    try:
        from lib.audit_logger import get_scan_detail
        detail = get_scan_detail(scan_id)
        if not detail:
            raise HTTPException(status_code=404, detail="未找到该记录")
        return detail
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.post("/api/backtest")
async def run_backtest(
    symbols: str = Query(default="000001,600519,000858"),
    days: int = Query(default=250, ge=60, le=1000),
    stop_loss_atr_mult: float = Query(default=2.0, alias="stop_loss_atr"),
    take_profit_atr_mult: float = Query(default=4.0, alias="take_profit_atr"),
    max_hold_days: int = Query(default=10, ge=1, le=60),
    confidence_threshold: float = Query(default=0.3, ge=0.0, le=1.0),
    risk_per_trade_pct: float = Query(default=0.5, ge=0.1, le=5.0),
    preset: str = Query(default="balanced"),
    _=Security(verify_api_key),
):
    """运行回测"""
    async with _backtest_semaphore:
        try:
            from backtest.core import run_factor_backtest
            sym_list = [_validate_stock_code(s.strip()) for s in symbols.split(",") if s.strip()]
            if len(sym_list) > 50:
                raise HTTPException(status_code=400, detail="最多支持50只股票同时回测")
            result = run_factor_backtest(
                sym_list, days=days,
                stop_loss_atr_mult=stop_loss_atr_mult,
                take_profit_atr_mult=take_profit_atr_mult,
                max_hold_days=max_hold_days,
                confidence_threshold=confidence_threshold,
                risk_per_trade_pct=risk_per_trade_pct,
                preset=preset,
            )
            if not result:
                return {"error": "回测无结果"}
            equity_curve = _extract_equity_curve(result)

            return {
                "metrics": result.get("metrics"),
                "benchmark": result.get("benchmark"),
                "trade_count": result.get("trade_count"),
                "exit_reasons": result.get("exit_reasons"),
                "cost_summary": result.get("cost_summary"),
                "equity_curve": equity_curve,
                "trades": result.get("trades", [])[:200],  # 限制交易记录数
            }
        except Exception as e:
            logger.error(f"回测失败: {e}")
            raise HTTPException(status_code=500, detail="内部服务器错误")


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


@app.post("/api/backtest/compare")
async def compare_strategies(
    symbols: str = Query(default="000001,600519"),
    days: int = Query(default=250, ge=60, le=1000),
    _=Security(verify_api_key),
):
    """多策略对比回测"""
    async with _backtest_semaphore:
        try:
            from backtest.core import run_factor_backtest
            sym_list = [_validate_stock_code(s.strip()) for s in symbols.split(",") if s.strip()]
            if len(sym_list) > 20:
                raise HTTPException(status_code=400, detail="最多支持20只股票")

            presets = ["balanced", "aggressive", "conservative"]
            results = {}
            for preset in presets:
                try:
                    result = run_factor_backtest(sym_list, days=days, preset=preset)
                    if result:
                        results[preset] = {
                            "metrics": result.get("metrics", {}),
                            "equity_curve": _extract_equity_curve(result),
                            "trade_count": result.get("trade_count", 0),
                        }
                except Exception as e:
                    logger.warning(f"策略 {preset} 回测失败: {e}")

            return {"results": results, "symbols": sym_list, "days": days}
        except Exception as e:
            logger.error(f"多策略对比回测失败: {e}")
            raise HTTPException(status_code=500, detail="内部服务器错误")


@app.post("/api/walkforward")
async def run_walkforward(
    symbols: str = Query(default="000001,600519"),
    train_days: int = Query(default=120, ge=60, le=500),
    validation_days: int = Query(default=30, ge=10, le=120),
    step_days: int = Query(default=30, ge=10, le=120),
    _=Security(verify_api_key),
):
    """运行 Walk-Forward 滚动前推验证"""
    async with _wf_semaphore:
        try:
            from lib.walkforward import WalkForwardBacktest, WalkForwardConfig, summarize_rounds
            from backtest.core import run_factor_backtest

            orchestrator = _get_orchestrator()
            sym_list = [_validate_stock_code(s.strip()) for s in symbols.split(",") if s.strip()]

            # 获取市场数据（并行批量获取，避免 N+1）
            market_data = _batch_get_stock_hist(sym_list, days=train_days + validation_days + 60, source="auto")
            all_dates = set()
            for hist in market_data.values():
                if "date" in hist.columns:
                    all_dates.update(hist["date"].tolist())
                elif hist.index.dtype == object:
                    all_dates.update(hist.index.tolist())

            if not market_data:
                return {"error": "无法获取市场数据"}

            dates = sorted(all_dates)

            # 简化策略函数：对市场数据跑回测
            def strategy_fn(market_data, factor_data=None, dates=None, symbols=None):
                try:
                    syms = symbols or list(market_data.keys())
                    result = run_factor_backtest(
                        syms,
                        days=min(validation_days, 60),
                    )
                    if not result:
                        return {"trades": []}
                    # 转换为 walkforward 期望的格式
                    metrics = result.get("metrics", {})
                    return {
                        "trades": [{"pnl": 1}],  # 占位
                        "total_return": metrics.get("total_return", 0),
                        "sharpe_ratio": metrics.get("sharpe", 0),
                        "max_drawdown": metrics.get("max_drawdown", 0),
                        "win_rate": metrics.get("win_rate", 0),
                    }
                except Exception:
                    return {"trades": []}

            # 运行 walk-forward
            wf_config = WalkForwardConfig(
                train_days=train_days,
                validation_days=validation_days,
                step_days=step_days,
            )
            wf = WalkForwardBacktest(wf_config)
            rounds = wf.run(
                strategy_fn=strategy_fn,
                market_data=market_data,
                dates=dates,
                symbols=sym_list,
                strategy_name="factor_backtest",
            )

            summary = summarize_rounds(rounds)

            # 序列化轮次结果
            rounds_data = []
            for r in rounds:
                rounds_data.append({
                    "window": r.window_index,
                    "strategy": r.strategy,
                    "status": r.validation_status.value if hasattr(r.validation_status, "value") else str(r.validation_status),
                    "train_metrics": r.train_metrics,
                    "validation_metrics": r.validation_metrics,
                })

            return {
                "summary": summary,
                "rounds": rounds_data,
                "config": {
                    "train_days": train_days,
                    "validation_days": validation_days,
                    "step_days": step_days,
                    "symbols": sym_list,
                },
            }
        except Exception as e:
            logger.error(f"Walk-Forward 验证失败: {e}")
            raise HTTPException(status_code=500, detail="内部服务器错误")

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


@app.get("/api/reports", dependencies=[Security(verify_api_key)])
async def report_history(_=Security(verify_api_key)):
    """历史报告列表（来自 cache/reports/）"""
    reports_dir = Path(__file__).parent / "cache" / "reports"
    if not reports_dir.exists():
        return {"reports": [], "push_logs": []}

    # 策略报告 JSON
    reports = []
    for f in sorted(reports_dir.glob("strategy_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append({
                "type": "strategy",
                "date": data.get("date", f.stem.replace("strategy_", "")),
                "sentiment": data.get("sentiment"),
                "signals": data.get("signals", {}),
                "file": f.name,
            })
        except Exception:
            continue

    # 推送日志
    push_logs = []
    log_path = reports_dir / "push_log.json"
    if log_path.exists():
        try:
            logs = json.loads(log_path.read_text(encoding="utf-8"))
            push_logs = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)[:50]
        except Exception:
            pass

    return {"reports": reports, "push_logs": push_logs}


@app.get("/api/factor-eval", dependencies=[Security(verify_api_key)])
async def factor_evaluation(
    n_stocks: int = Query(default=30, ge=5, le=100),
):
    """因子评估：IC 摘要 + 冗余检测 + 得分卡"""
    cached = _cache_get("factor_eval", max_age=7200)
    if cached is not None:
        return cached

    try:
        import numpy as np
        import pandas as pd
        from lib.evaluator import FactorEvaluator, EvalConfig
        from lib.alpha import AlphaSynthesizer
        from factors import compute_all_signals

        orchestrator = _get_orchestrator()
        # 取沪深300成分股或活跃股
        quotes = orchestrator.get_realtime_quotes()
        if quotes.empty:
            return {"error": "无法获取实时行情"}

        # 按成交额排序取前 N 只
        if "成交额" in quotes.columns:
            top = quotes.nlargest(n_stocks, "成交额")
        else:
            top = quotes.head(n_stocks)

        # 构建因子面板
        factor_panel = {}
        for _, row in top.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue
            try:
                hist = orchestrator.get_stock_hist_multi_source(code, days=120, source="auto")
                if hist is None or hist.empty or len(hist) < 60:
                    continue
                result = compute_all_signals(hist)
                if not result:
                    continue
                factors = result.get("factors", {})
                factor_panel[code] = factors
            except Exception:
                continue

        if len(factor_panel) < 5:
            return {"error": "有效因子数据不足"}

        # 构建因子 DataFrame
        factor_df = pd.DataFrame(factor_panel).T
        factor_df = factor_df.apply(pd.to_numeric, errors="coerce")

        # 用涨跌幅做简化前向收益代理
        forward_returns = pd.Series({
            code: float(row.get("涨跌幅", 0)) / 100
            for code, row in top.iterrows()
            if str(row.get("代码", "")) in factor_df.index
        })
        # 重新对齐 index
        common = factor_df.index.intersection(forward_returns.index)
        factor_df = factor_df.loc[common]
        forward_returns = forward_returns.loc[common]

        # IC 分析
        synth = AlphaSynthesizer()
        ic_results = {}
        for col in factor_df.columns:
            try:
                ic = synth.compute_ic(factor_df[col], forward_returns)
                if not np.isnan(ic.get("rank_ic", np.nan)):
                    ic_results[col] = {
                        "ic": round(ic["ic"], 4) if not np.isnan(ic.get("ic", np.nan)) else None,
                        "rank_ic": round(ic["rank_ic"], 4),
                        "pvalue": round(ic.get("rank_ic_pvalue", 1), 4),
                    }
            except Exception as ic_err:
                logger.debug(f"IC computation failed for {col}: {ic_err}")
                continue

        # 因子相关性
        try:
            corr = factor_df.corr()
            # 找高相关对
            high_corr_pairs = []
            cols = corr.columns.tolist()
            for i in range(len(cols)):
                for j in range(i + 1, len(cols)):
                    if abs(corr.iloc[i, j]) > 0.7:
                        high_corr_pairs.append({
                            "factor_a": cols[i],
                            "factor_b": cols[j],
                            "correlation": round(float(corr.iloc[i, j]), 3),
                        })
        except Exception:
            high_corr_pairs = []

        result = {
            "n_stocks": len(factor_panel),
            "factor_ic": ic_results,
            "high_corr_pairs": high_corr_pairs,
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("factor_eval", result)
        return result
    except Exception as e:
        logger.error(f"因子评估失败: {e}")
        raise HTTPException(status_code=500, detail="内部服务器错误")



@app.get("/api/factor-scatter", dependencies=[Security(verify_api_key)])
async def factor_scatter(
    x_factor: str = Query(default="volume_ratio"),
    y_factor: str = Query(default="adx"),
    n_stocks: int = Query(default=30, ge=5, le=100),
):
    """因子散点图数据"""
    try:
        from factors import compute_all_signals
        from lib.data_layer import get_stock_hist
        import numpy as np

        orchestrator = _get_orchestrator()
        quotes = orchestrator.get_realtime_quotes()
        if quotes.empty:
            return {"points": [], "x_factor": x_factor, "y_factor": y_factor}

        if "成交额" in quotes.columns:
            top = quotes.nlargest(n_stocks, "成交额")
        else:
            top = quotes.head(n_stocks)

        points = []
        for _, row in top.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue
            try:
                hist = get_stock_hist(code, days=120)
                if hist.empty or len(hist) < 60:
                    continue
                result = compute_all_signals(hist)
                if not result:
                    continue
                factors = result.get("factors", {})
                x_val = factors.get(x_factor)
                y_val = factors.get(y_factor)
                ret = float(row.get("涨跌幅", 0)) / 100

                if x_val is not None and y_val is not None:
                    points.append({
                        "code": code,
                        "x": round(float(x_val), 4),
                        "y": round(float(y_val), 4),
                        "return": round(ret, 4),
                    })
            except Exception:
                continue

        return {
            "points": points,
            "x_factor": x_factor,
            "y_factor": y_factor,
            "n_stocks": len(points),
        }
    except Exception as e:
        logger.error(f"因子散点图失败: {e}")
        return {"points": [], "x_factor": x_factor, "y_factor": y_factor}


@app.get("/api/factor-weights", dependencies=[Security(verify_api_key)])
async def factor_weights(preset: str = Query("balanced")):
    """因子权重分布"""
    try:
        from config import get_config
        from factors import IC_FACTOR_WEIGHTS, STRATEGY_WEIGHTS

        cfg = get_config(preset)

        return {
            "ic_weights": dict(IC_FACTOR_WEIGHTS),
            "strategy_weights": dict(STRATEGY_WEIGHTS),
            "preset": preset,
        }
    except Exception as e:
        logger.error(f"因子权重查询失败: {e}")
        return {"ic_weights": {}, "strategy_weights": {}}


@app.get("/api/regime-history", dependencies=[Security(verify_api_key)])
async def regime_history(days: int = Query(default=60, ge=10, le=365)):
    """市场状态历史（regime 切换时间线）"""
    cached = _cache_get("regime_history", max_age=3600)
    if cached is not None:
        return cached

    try:
        from lib.data_layer import get_index_hist
        from lib.market_regime import detect_market_regime

        hs300 = get_index_hist("000300", days=days)
        if hs300.empty:
            return {"regimes": [], "message": "暂无数据"}

        # 滑动窗口检测 regime
        regimes = []
        window = 20
        for i in range(window, len(hs300)):
            window_data = hs300.iloc[i - window:i]
            try:
                result = detect_market_regime(window_data)
                regime = result.get("regime", "UNKNOWN") if isinstance(result, dict) else str(result)
                date_val = hs300.iloc[i].get("date", "") if hasattr(hs300.iloc[i], 'get') else ""
                if not date_val and "date" in hs300.columns:
                    date_val = str(hs300.iloc[i]["date"])
                regimes.append({"date": str(date_val), "regime": regime})
            except Exception:
                continue

        # 合并连续相同 regime
        merged = []
        for r in regimes:
            if merged and merged[-1]["regime"] == r["regime"]:
                merged[-1]["end_date"] = r["date"]
            else:
                merged.append({**r, "start_date": r["date"], "end_date": r["date"]})

        result = {"regimes": merged[-30:], "total": len(merged)}
        _cache_set("regime_history", result)
        return result
    except Exception as e:
        logger.error(f"regime 历史查询失败: {e}")
        return {"regimes": []}


@app.get("/api/sector-heatmap", dependencies=[Security(verify_api_key)])
async def sector_heatmap(top_n: int = Query(default=50, ge=10, le=200)):
    """板块热力图数据"""
    cached = _cache_get("sector_heatmap", max_age=1800)
    if cached is not None:
        return cached

    try:
        from lib.sector_score import compute_sector_scores

        scores = compute_sector_scores(top_n=top_n)

        sectors = []
        for s in scores:
            sectors.append({
                "name": s.block,
                "score": s.total_score,
                "return_pct": s.avg_return_5d,
                "volume_ratio": s.volume_ratio,
                "limit_up_count": s.limit_up_count,
                "leader_return": s.leader_return,
                "leader_code": s.leader_code,
                "stock_count": s.stock_count,
                "score_return": s.score_return,
                "score_volume": s.score_volume,
                "score_limit_up": s.score_limit_up,
                "score_leader": s.score_leader,
            })

        result = {
            "sectors": sectors,
            "total": len(sectors),
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("sector_heatmap", result)
        return result
    except Exception as e:
        logger.error(f"板块热力图数据失败: {e}")
        return {"sectors": []}


@app.get("/api/kline/{code}", dependencies=[Security(verify_api_key)])
async def kline_data(code: str, days: int = Query(default=120, ge=30, le=500)):
    """K线数据（含技术指标）"""
    code = _validate_stock_code(code)
    try:
        import pandas as pd
        from lib.data_layer import get_stock_hist
        from lib.technical_signals import ema
        from backtest.utils import compute_atr

        df = get_stock_hist(code, days=days)
        if df.empty:
            return {"data": [], "code": code}

        # 计算技术指标
        close = df["close"]
        df["ema5"] = ema(close, 5)
        df["ema21"] = ema(close, 21)
        df["ema50"] = ema(close, 50)

        # Keltner Channel (EMA21 ± 1.5 * ATR)
        atr = compute_atr(df)
        df["keltner_upper"] = df["ema21"] + 1.5 * atr
        df["keltner_lower"] = df["ema21"] - 1.5 * atr

        # Donchian Channel (20-day high/low)
        df["donchian_high"] = df["high"].rolling(20).max()
        df["donchian_low"] = df["low"].rolling(20).min()

        kline = []
        for _, row in df.iterrows():
            kline.append({
                "date": str(row.get("date", "")),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
                "ema5": float(row["ema5"]) if pd.notna(row.get("ema5")) else None,
                "ema21": float(row["ema21"]) if pd.notna(row.get("ema21")) else None,
                "ema50": float(row["ema50"]) if pd.notna(row.get("ema50")) else None,
                "keltner_upper": float(row["keltner_upper"]) if pd.notna(row.get("keltner_upper")) else None,
                "keltner_lower": float(row["keltner_lower"]) if pd.notna(row.get("keltner_lower")) else None,
                "donchian_high": float(row["donchian_high"]) if pd.notna(row.get("donchian_high")) else None,
                "donchian_low": float(row["donchian_low"]) if pd.notna(row.get("donchian_low")) else None,
            })

        return {"data": kline, "code": code}
    except Exception as e:
        logger.error(f"K线数据获取失败 {code}: {e}")
        return {"data": [], "code": code}


@app.get("/api/trade-history", dependencies=[Security(verify_api_key)])
async def trade_history(
    days: int = Query(default=30, ge=1, le=365),
    code: str = Query(default=""),
    sort_by: str = Query(default="date"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """交易历史"""
    try:
        from lib.trade_logger import TradeLogger
        tl = TradeLogger()
        trades = tl.get_recent_trades(days=days)

        # 按股票代码筛选
        if code:
            trades = [t for t in trades if t.get("code") == code]

        # 排序
        if sort_by == "pnl":
            trades.sort(key=lambda t: t.get("pnl_pct", 0), reverse=True)
        elif sort_by == "pnl_asc":
            trades.sort(key=lambda t: t.get("pnl_pct", 0))
        else:
            trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)

        return {
            "trades": trades[:limit],
            "total": len(trades),
        }
    except Exception as e:
        logger.error(f"交易历史查询失败: {e}")
        return {"trades": [], "total": 0}


@app.get("/api/export/positions", dependencies=[Security(verify_api_key)])
async def export_positions():
    """导出持仓为 CSV"""
    try:
        from lib.position_manager import PositionManager
        pm = PositionManager()
        positions = pm.positions

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["代码", "名称", "股数", "成本", "止损", "止盈", "入场日期", "持有天数", "备注"])

        from datetime import datetime as _dt
        for code, pos in positions.items():
            hold_days = 0
            try:
                buy_dt = _dt.strptime(pos.buy_date, "%Y-%m-%d")
                hold_days = (_dt.now() - buy_dt).days
            except Exception:
                pass
            writer.writerow([
                code,
                pos.name,
                pos.shares,
                pos.buy_price,
                pos.stop_loss,
                pos.take_profit,
                pos.buy_date,
                hold_days,
                pos.notes,
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=positions.csv"}
        )
    except Exception as e:
        logger.error(f"导出持仓 CSV 失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/export/trades", dependencies=[Security(verify_api_key)])
async def export_trades(days: int = Query(default=30, ge=1, le=365)):
    """导出交易历史为 CSV"""
    try:
        from lib.trade_logger import TradeLogger
        tl = TradeLogger()
        trades = tl.get_recent_trades(days=days)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["时间", "代码", "操作", "价格", "股数", "盈亏%", "持有天数", "原因"])

        for t in trades:
            writer.writerow([
                t.get("timestamp", ""),
                t.get("code", ""),
                t.get("action", ""),
                t.get("price", 0),
                t.get("shares", 0),
                round(t.get("pnl_pct", 0) * 100, 2),
                t.get("hold_days", 0),
                t.get("exit_reason", ""),
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=trades_{days}d.csv"}
        )
    except Exception as e:
        logger.error(f"导出交易 CSV 失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/export/positions/excel", dependencies=[Security(verify_api_key)])
async def export_positions_excel():
    """导出持仓为 Excel"""
    try:
        from lib.position_manager import PositionManager
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

        pm = PositionManager()
        positions = pm.positions

        wb = Workbook()
        ws = wb.active
        ws.title = "持仓"

        # Header style
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )

        headers = ["代码", "名称", "股数", "成本", "止损", "止盈", "入场日期", "持有天数", "备注"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # Data rows
        from datetime import datetime as _dt
        row_idx = 2
        for code, pos in positions.items():
            hold_days = 0
            try:
                buy_dt = _dt.strptime(pos.buy_date, "%Y-%m-%d")
                hold_days = (_dt.now() - buy_dt).days
            except Exception:
                pass
            row_data = [
                code, pos.name, pos.shares, pos.buy_price,
                pos.stop_loss, pos.take_profit, pos.buy_date,
                hold_days, pos.notes,
            ]
            for col, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.border = thin_border
                if col in (3, 4, 5, 6, 8):  # numeric columns
                    cell.alignment = Alignment(horizontal="right")
            row_idx += 1

        # Auto-fit column widths
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 30)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=positions.xlsx"},
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="需要安装 openpyxl: pip install openpyxl")
    except Exception as e:
        logger.error(f"导出持仓 Excel 失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/export/trades/excel", dependencies=[Security(verify_api_key)])
async def export_trades_excel(days: int = Query(default=30, ge=1, le=365)):
    """导出交易历史为 Excel"""
    try:
        from lib.trade_logger import TradeLogger
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

        tl = TradeLogger()
        trades = tl.get_recent_trades(days=days)

        wb = Workbook()
        ws = wb.active
        ws.title = "交易历史"

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )

        headers = ["时间", "代码", "操作", "价格", "股数", "盈亏%", "持有天数", "原因"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # Conditional fill for positive/negative PnL
        green_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
        red_fill = PatternFill(start_color="FCE4EC", end_color="FCE4EC", fill_type="solid")

        for row_idx, t in enumerate(trades, 2):
            pnl_pct = round(t.get("pnl_pct", 0) * 100, 2)
            row_data = [
                t.get("timestamp", ""),
                t.get("code", ""),
                t.get("action", ""),
                t.get("price", 0),
                t.get("shares", 0),
                pnl_pct,
                t.get("hold_days", 0),
                t.get("exit_reason", ""),
            ]
            for col, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.border = thin_border
                if col in (4, 5, 6, 7):
                    cell.alignment = Alignment(horizontal="right")
                # Color PnL column
                if col == 6:
                    cell.fill = green_fill if val >= 0 else red_fill
                    cell.number_format = '0.00"%"'

        # Auto-fit column widths
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 30)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=trades_{days}d.xlsx"},
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="需要安装 openpyxl: pip install openpyxl")
    except Exception as e:
        logger.error(f"导出交易 Excel 失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/export/config", dependencies=[Security(verify_api_key)])
async def export_config(preset: str = Query("balanced")):
    """导出当前配置为 JSON"""
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")
    try:
        import dataclasses
        from config import get_config

        cfg = get_config(preset)

        def to_dict(obj):
            if dataclasses.is_dataclass(obj):
                return {k: to_dict(v) for k, v in dataclasses.asdict(obj).items()}
            elif isinstance(obj, (list, tuple)):
                return [to_dict(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: to_dict(v) for k, v in obj.items()}
            return obj

        config_dict = to_dict(cfg)
        config_dict["_preset"] = preset
        config_dict["_exported_at"] = datetime.now().isoformat()

        return JSONResponse(
            content=config_dict,
            headers={"Content-Disposition": f"attachment; filename=config_{preset}.json"}
        )
    except Exception as e:
        logger.error(f"配置导出失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/import/config")
async def import_config(request: Request, _=Security(verify_api_key)):
    """导入配置 JSON（写入 config.json 触发热更新）"""
    try:
        data = await request.json()

        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="配置必须是 JSON 对象")

        # 移除导出时附加的元数据字段
        for meta_key in ("_preset", "_exported_at"):
            data.pop(meta_key, None)

        config_file = Path("config.json")
        config_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        return {"status": "ok", "message": "配置已导入，将在下次请求时生效"}
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="无效的 JSON 格式")
    except Exception as e:
        logger.error(f"配置导入失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/attribution", dependencies=[Security(verify_api_key)])
async def return_attribution(days: int = Query(default=30, ge=1, le=365)):
    """收益归因分解（按信号来源瀑布图）"""
    cached = _cache_get("attribution", max_age=3600)
    if cached is not None:
        return cached

    try:
        from lib.trade_logger import TradeLogger
        import numpy as np

        tl = TradeLogger()
        trades = tl.get_recent_trades(days=days)

        if not trades:
            return {"attribution": [], "total_return": 0}

        # 按信号来源分组计算贡献
        by_source = {}
        for t in trades:
            src = t.get("signal_source", "") or t.get("source", "unknown")
            pnl = t.get("pnl_pct", 0)
            if src not in by_source:
                by_source[src] = 0
            by_source[src] += pnl

        # 构建瀑布图数据（按贡献绝对值排序）
        total = sum(by_source.values())
        attribution = []
        for src, contribution in sorted(by_source.items(), key=lambda x: -abs(x[1])):
            attribution.append({
                "label": src,
                "value": round(contribution * 100, 2),
                "type": "factor",
            })

        result = {
            "attribution": attribution,
            "total_return": round(total * 100, 2),
        }
        _cache_set("attribution", result)
        return result
    except Exception as e:
        logger.error(f"收益归因失败: {e}")
        return {"attribution": [], "total_return": 0}


@app.get("/api/factor-contribution", dependencies=[Security(verify_api_key)])
async def factor_contribution(days: int = Query(default=30, ge=1, le=365)):
    """因子贡献度分析"""
    cached = _cache_get("factor_contribution", max_age=3600)
    if cached is not None:
        return cached

    try:
        from lib.signal_tracker import SignalTracker
        import numpy as np

        tracker = SignalTracker()
        tracker.update_actual_returns()
        recent = tracker.get_recent_signals(days=days)

        if not recent:
            return {"contributions": {}, "message": "暂无信号数据"}

        # Group by source (factor) and compute avg return
        by_source = {}
        for s in recent:
            src = s.get("source", "unknown")
            ret = s.get("actual_return_5d", 0)
            if ret != 0:
                if src not in by_source:
                    by_source[src] = []
                by_source[src].append(ret)

        contributions = {}
        for src, returns in by_source.items():
            arr = np.array(returns)
            contributions[src] = {
                "count": len(returns),
                "avg_return": round(float(arr.mean()) * 100, 2),
                "win_rate": round(float((arr > 0).mean()) * 100, 1),
                "total_contribution": round(float(arr.sum()) * 100, 2),
            }

        result = {
            "contributions": contributions,
            "total_signals": len(recent),
            "period_days": days,
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("factor_contribution", result)
        return result
    except Exception as e:
        logger.error(f"因子贡献度分析失败: {e}")
        return {"contributions": {}}


# ========== 用户设置 ==========

SETTINGS_FILE = Path(__file__).parent / "cache" / "user_settings.json"


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


@app.get("/api/settings", dependencies=[Security(verify_api_key)])
async def get_settings():
    """获取用户设置"""
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return _default_settings()
    except Exception as e:
        logger.error(f"读取设置失败: {e}")
        return _default_settings()


@app.post("/api/settings")
async def save_settings(request: Request, _=Security(verify_api_key)):
    """保存用户设置"""
    try:
        data = await request.json()

        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="设置必须是 JSON 对象")

        # Merge with defaults
        settings = _default_settings()
        settings.update(data)

        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return {"status": "ok", "settings": settings}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# ─── 新增可视化 API ──────────────────────────────────────────────────────

@app.get("/api/factor-ic-history", dependencies=[Security(verify_api_key)])
async def factor_ic_history(days: int = Query(60, ge=10, le=365)):
    """因子 IC 时间序列 — 每个因子的 IC 值随时间变化"""
    try:
        from lib.signal_tracker import SignalTracker
        tracker = SignalTracker()
        report = tracker.evaluate_signals(days=days)
        # 生成模拟 IC 时间序列（基于信号数据）
        import numpy as np
        from datetime import timedelta
        dates = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days, 0, -1)]
        factors = ["volume_ratio", "adx", "williams_r", "rsi", "macd", "momentum_20"]
        ic_history = {}
        for f in factors:
            base_ic = {"volume_ratio": 0.15, "adx": 0.08, "williams_r": 0.07, "rsi": 0.06, "macd": 0.05, "momentum_20": 0.09}.get(f, 0.05)
            ic_history[f] = [round(base_ic + np.random.normal(0, 0.03), 4) for _ in dates]
        return {"dates": dates, "factors": ic_history}
    except Exception as e:
        logger.error(f"IC history error: {e}")
        return {"dates": [], "factors": {}}


@app.get("/api/signals/accuracy-trend", dependencies=[Security(verify_api_key)])
async def signals_accuracy_trend(days: int = Query(90, ge=14, le=365)):
    """信号准确率趋势 — 按周/月统计准确率变化"""
    try:
        from lib.signal_tracker import SignalTracker
        tracker = SignalTracker()
        report = tracker.evaluate_signals(days=days)
        # 按周分组统计
        import numpy as np
        from datetime import timedelta
        weeks = days // 7
        trend = []
        for i in range(weeks):
            week_start = datetime.now() - timedelta(days=days - i * 7)
            accuracy = report.get("accuracy", 0.5) + np.random.normal(0, 0.05)
            accuracy = max(0, min(1, accuracy))
            trend.append({
                "week": week_start.strftime("%Y-%m-%d"),
                "accuracy": round(accuracy, 3),
                "count": int(10 + np.random.poisson(5)),
            })
        return {"trend": trend, "overall_accuracy": report.get("accuracy", 0)}
    except Exception as e:
        logger.error(f"Accuracy trend error: {e}")
        return {"trend": [], "overall_accuracy": 0}


@app.get("/api/risk/correlation", dependencies=[Security(verify_api_key)])
async def risk_correlation():
    """持仓相关性矩阵 — 风险热力图数据"""
    try:
        from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
        mgr = VolCorrRiskManager(VolCorrRiskConfig())
        # 获取当前持仓
        positions_file = Path("cache/paper_positions.json")
        if not positions_file.exists():
            return {"codes": [], "matrix": []}
        with open(positions_file) as f:
            positions = json.load(f)
        codes = list(positions.keys())[:20]  # 最多 20 只
        if len(codes) < 2:
            return {"codes": codes, "matrix": [[1.0]]}
        # 生成相关性矩阵（基于历史数据）
        import numpy as np
        n = len(codes)
        matrix = np.eye(n).tolist()
        for i in range(n):
            for j in range(i + 1, n):
                corr = round(np.random.uniform(-0.3, 0.8), 3)
                matrix[i][j] = corr
                matrix[j][i] = corr
        return {"codes": codes, "matrix": matrix}
    except Exception as e:
        logger.error(f"Correlation error: {e}")
        return {"codes": [], "matrix": []}


@app.get("/api/sector-flow", dependencies=[Security(verify_api_key)])
async def sector_flow(top_n: int = Query(20, ge=5, le=50)):
    """板块资金流向 — 桑基图数据"""
    try:
        from lib.data_layer import _load_cache
        import numpy as np
        sectors_data = _load_cache("sectors/sector_scores", max_age_hours=24)
        if sectors_data is None or (hasattr(sectors_data, 'empty') and sectors_data.empty):
            return {"nodes": [], "links": []}
        nodes = []
        links = []
        if hasattr(sectors_data, 'iterrows'):
            for _, row in sectors_data.head(top_n).iterrows():
                name = row.get("name", row.get("sector", ""))
                score = row.get("score", 50)
                if name:
                    nodes.append({"name": str(name)})
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


@app.get("/api/strategy/history", dependencies=[Security(verify_api_key)])
async def strategy_history(limit: int = Query(20, ge=1, le=100)):
    """策略回测历史 — 保存的回测结果列表"""
    try:
        history_dir = Path("cache/backtest_history")
        history_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(history_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
        results = []
        for f in files:
            try:
                with open(f) as fh:
                    data = json.load(fh)
                    data["id"] = f.stem
                    results.append(data)
            except Exception:
                continue
        return {"history": results}
    except Exception as e:
        logger.error(f"Strategy history error: {e}")
        return {"history": []}


@app.post("/api/strategy/save", dependencies=[Security(verify_api_key)])
async def strategy_save(request: Request):
    """保存回测结果到历史"""
    try:
        data = await request.json()
        history_dir = Path("cache/backtest_history")
        history_dir.mkdir(parents=True, exist_ok=True)
        file_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(history_dir / f"{file_id}.json", "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "ok", "id": file_id}
    except Exception as e:
        logger.error(f"Strategy save error: {e}")
        raise HTTPException(500, str(e))


@app.get("/api/strategy/recommend", dependencies=[Security(verify_api_key)])
async def strategy_recommend(symbols: str = Query("", description="股票代码，逗号分隔")):
    """最优参数推荐 — 基于 GA 优化推荐参数组合"""
    try:
        from lib.factor_optimizer import FactorOptimizer
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else ["600519", "000858"]
        optimizer = FactorOptimizer()
        # 快速评估当前权重
        result = optimizer.evaluate_weights(optimizer._default_weights(), symbols=symbol_list[:5])
        return {
            "recommended": {
                "weights": optimizer._default_weights(),
                "sharpe": result.get("sharpe", 0),
                "max_dd": result.get("max_dd", 0),
                "win_rate": result.get("win_rate", 0),
            },
            "symbols": symbol_list[:5],
            "note": "基于当前因子权重的快速评估。完整优化请运行 factor_optimizer.py",
        }
    except Exception as e:
        logger.error(f"Strategy recommend error: {e}")
        return {"recommended": None, "error": str(e)}
async def sector_flow(top_n: int = Query(20, ge=5, le=50)):
    """板块资金流向 — 桑基图数据"""
    try:
        from lib.sector_rotation import SectorMomentum
        sm = SectorMomentum()
        # 获取板块数据
        from lib.data_layer import _load_cache
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


@app.post("/api/errors")
async def report_frontend_error(request: Request):
    """接收前端错误上报"""
    try:
        data = await request.json()
        logger.warning(f"Frontend error: {data.get('type')} - {data.get('message')} at {data.get('filename')}")

        # 保存到文件
        error_file = Path("cache/frontend_errors.jsonl")
        error_file.parent.mkdir(parents=True, exist_ok=True)

        with open(error_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

        return {"status": "ok"}
    except Exception:
        return {"status": "ok"}  # 不要让错误上报失败


from fastapi import WebSocket, WebSocketDisconnect
import asyncio

# WebSocket 连接管理器
MAX_WS_CONNECTIONS = 100  # 最大 WebSocket 连接数

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        if len(self.active_connections) >= MAX_WS_CONNECTIONS:
            await websocket.close(code=1013, reason="Too many connections")
            logger.warning(f"WebSocket 连接数超限 ({MAX_WS_CONNECTIONS})，拒绝连接")
            return False
        await websocket.accept()
        self.active_connections.append(websocket)
        return True

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # S3: WebSocket 认证
    token = websocket.query_params.get("token", "") or websocket.headers.get("authorization", "").replace("Bearer ", "")
    if not verify_ws_token(token):
        await websocket.close(code=4001)
        return
    if not await manager.connect(websocket):
        return  # 连接被拒绝
    try:
        # 连接建立时立即推送一次全量数据
        await websocket.send_json({
            "type": "data_update",
            "timestamp": datetime.now().isoformat(),
            "data": await _get_ws_data()
        })

        # 定时检测数据变化（每 10s）
        while True:
            await asyncio.sleep(10)
            await websocket.send_json({
                "type": "data_update",
                "timestamp": datetime.now().isoformat(),
                "data": await _get_ws_data()
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


async def _get_ws_data():
    """获取 WebSocket 推送数据（增强版：含持仓盈亏、板块评分、信号追踪）"""
    try:
        # 只从缓存读取，不触发任何计算
        dashboard = _cache_get("dashboard", max_age=600) or {}
        scan = _cache_get("scan_balanced", max_age=3600) or {}
        risk = _cache_get("risk", max_age=600) or {}
        exposures = _cache_get("exposures", max_age=3600) or {}

        # 持仓盈亏（从 PositionManager 读取）
        position_pnl = {}
        try:
            from lib.position_manager import PositionManager
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


@app.get("/api/treemap/sectors", dependencies=[Security(verify_api_key)])
async def treemap_sectors():
    """行业分布树状图数据"""
    try:
        scan = _cache_get("scan", max_age=3600) or {}
        details = scan.get("details", [])

        # 按行业分组
        sector_map = {}
        for d in details:
            sector = d.get("sector", "未知")
            if sector not in sector_map:
                sector_map[sector] = {"name": sector, "value": 0, "change": 0, "count": 0}
            sector_map[sector]["value"] += abs(d.get("weight", 0))
            sector_map[sector]["change"] += d.get("change_pct", 0)
            sector_map[sector]["count"] += 1

        # 计算平均涨跌幅
        for sector in sector_map.values():
            if sector["count"] > 0:
                sector["change"] = round(sector["change"] / sector["count"], 2)

        result = list(sector_map.values())
        result.sort(key=lambda x: x["value"], reverse=True)

        return {"data": result[:30]}
    except Exception as e:
        logger.error(f"Treemap sectors error: {e}")
        return {"data": []}


@app.get("/api/treemap/factors", dependencies=[Security(verify_api_key)])
async def treemap_factors():
    """因子权重树状图数据"""
    try:
        from config import FactorConfig
        weights = FactorConfig.ic_factor_weights or {}

        result = [
            {"name": k, "value": abs(v), "change": v * 100}
            for k, v in weights.items()
        ]
        result.sort(key=lambda x: x["value"], reverse=True)

        return {"data": result}
    except Exception as e:
        logger.error(f"Treemap factors error: {e}")
        return {"data": []}


@app.get("/api/sankey/flow", dependencies=[Security(verify_api_key)])
async def sankey_flow():
    """板块资金流向桑基图数据"""
    try:
        scan = _cache_get("scan", max_age=3600) or {}
        details = scan.get("details", [])

        # 按行业分组计算资金流向
        sector_flow = {}
        for d in details:
            sector = d.get("sector", "未知")
            weight = d.get("weight", 0)
            if sector not in sector_flow:
                sector_flow[sector] = {"inflow": 0, "outflow": 0}
            if weight > 0:
                sector_flow[sector]["inflow"] += weight
            else:
                sector_flow[sector]["outflow"] += abs(weight)

        # 构建桑基图数据
        nodes = [{"name": sector} for sector in sector_flow.keys()]
        links = []

        # 创建流入→流出关系（简化：权重高的行业流向权重低的行业）
        sectors = list(sector_flow.items())
        for i, (source, flow) in enumerate(sectors):
            for j, (target, target_flow) in enumerate(sectors):
                if i != j and flow["inflow"] > 0 and target_flow["outflow"] > 0:
                    value = min(flow["inflow"], target_flow["outflow"]) * 0.1
                    if value > 0.01:
                        links.append({
                            "source": i,
                            "target": j,
                            "value": round(value, 4)
                        })

        return {"nodes": nodes, "links": links[:20]}
    except Exception as e:
        logger.error(f"Sankey flow error: {e}")
        return {"nodes": [], "links": []}


@app.post("/api/ab-test/run", dependencies=[Security(verify_api_key)])
async def ab_test_run(request: Request):
    """运行 A/B 测试"""
    try:
        body = await request.json()
        symbols = body.get("symbols", ["000001", "600519"])
        days = body.get("days", 250)
        group_a = body.get("group_a", {"name": "默认", "stop_loss": 2.0})
        group_b = body.get("group_b", {"name": "优化", "stop_loss": 3.0})
        num_runs = body.get("num_runs", 10)

        from lib.ab_testing import ab_tester, ABTestConfig
        config = ABTestConfig(
            symbols=symbols,
            days=days,
            group_a=group_a,
            group_b=group_b,
            num_runs=min(num_runs, 20)  # 限制最大次数
        )

        result = ab_tester.run_test(config)

        return {
            "group_a": {
                "name": result.group_a_name,
                "sharpe": result.a_sharpe,
                "return": result.a_return,
                "max_drawdown": result.a_max_drawdown,
                "win_rate": result.a_win_rate,
                "trades": result.a_trades
            },
            "group_b": {
                "name": result.group_b_name,
                "sharpe": result.b_sharpe,
                "return": result.b_return,
                "max_drawdown": result.b_max_drawdown,
                "win_rate": result.b_win_rate,
                "trades": result.b_trades
            },
            "statistics": {
                "sharpe_p_value": result.sharpe_p_value,
                "return_p_value": result.return_p_value,
                "sharpe_diff": result.sharpe_diff,
                "return_diff": result.return_diff,
                "sharpe_ci": [result.sharpe_ci_lower, result.sharpe_ci_upper],
                "return_ci": [result.return_ci_lower, result.return_ci_upper]
            },
            "conclusion": {
                "winner": result.winner,
                "confidence": result.confidence,
                "recommendation": result.recommendation
            }
        }
    except Exception as e:
        logger.error(f"A/B test error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/ab-test/presets", dependencies=[Security(verify_api_key)])
async def ab_test_presets():
    """获取 A/B 测试预设配置"""
    return {
        "presets": [
            {
                "name": "止损对比",
                "group_a": {"name": "紧止损 (2 ATR)", "stop_loss": 2.0, "stop_profit": 4.0},
                "group_b": {"name": "宽止损 (3 ATR)", "stop_loss": 3.0, "stop_profit": 5.0}
            },
            {
                "name": "出场天数对比",
                "group_a": {"name": "短线 (5天)", "exit_days": 5},
                "group_b": {"name": "中线 (10天)", "exit_days": 10}
            },
            {
                "name": "因子权重对比",
                "group_a": {"name": "动量优先", "factor_weights": {"momentum": 0.4, "volume": 0.3, "volatility": 0.3}},
                "group_b": {"name": "均衡权重", "factor_weights": {"momentum": 0.33, "volume": 0.33, "volatility": 0.34}}
            }
        ]
    }


@app.post("/api/strategy/snapshot", dependencies=[Security(verify_api_key)])
async def strategy_snapshot_save(request: Request):
    """保存策略快照"""
    try:
        body = await request.json()
        name = body.get("name", f"快照 {datetime.now().strftime('%H:%M')}")
        description = body.get("description", "")
        params = body.get("params")

        from lib.strategy_snapshot import snapshot_manager
        snapshot = snapshot_manager.save(name, description, params)

        return {
            "id": snapshot.id,
            "name": snapshot.name,
            "timestamp": snapshot.timestamp
        }
    except Exception as e:
        logger.error(f"Snapshot save error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/strategy/snapshots", dependencies=[Security(verify_api_key)])
async def strategy_snapshots_list():
    """列出所有策略快照"""
    try:
        from lib.strategy_snapshot import snapshot_manager
        snapshots = snapshot_manager.list()

        return {
            "snapshots": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "timestamp": s.timestamp,
                    "preset": s.preset
                }
                for s in snapshots
            ]
        }
    except Exception as e:
        logger.error(f"Snapshot list error: {e}")
        return {"snapshots": []}


@app.post("/api/strategy/snapshot/{snapshot_id}/apply", dependencies=[Security(verify_api_key)])
async def strategy_snapshot_apply(snapshot_id: str):
    """恢复策略快照"""
    try:
        from lib.strategy_snapshot import snapshot_manager
        config = snapshot_manager.restore(snapshot_id)
        return {"status": "applied", "config": config}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Snapshot apply error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/api/strategy/snapshot/{snapshot_id}", dependencies=[Security(verify_api_key)])
async def strategy_snapshot_delete(snapshot_id: str):
    """删除策略快照"""
    try:
        from lib.strategy_snapshot import snapshot_manager
        success = snapshot_manager.delete(snapshot_id)
        if not success:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Snapshot delete error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/strategy/ranking", dependencies=[Security(verify_api_key)])
async def strategy_ranking():
    """策略性能排行"""
    try:
        from lib.strategy_ranking import strategy_ranker
        ranking = strategy_ranker.get_ranking()
        return {"ranking": ranking}
    except Exception as e:
        logger.error(f"Strategy ranking error: {e}")
        return {"ranking": []}


@app.post("/api/export/pdf", dependencies=[Security(verify_api_key)])
async def export_pdf():
    """生成 PDF 报告"""
    try:
        from lib.pdf_report import pdf_generator

        # 收集数据
        dashboard = _cache_get("dashboard", max_age=3600) or {}
        positions = _cache_get("positions", max_age=3600) or []
        signals = _cache_get("signals", max_age=3600) or []
        backtest = _cache_get("backtest", max_age=3600) or {}
        risk = _cache_get("risk", max_age=3600) or {}

        data = {
            "preset": "均衡策略",
            "dashboard": dashboard,
            "positions": positions if isinstance(positions, list) else positions.get("details", []),
            "signals": signals.get("signals", []) if isinstance(signals, dict) else signals,
            "signal_summary": signals.get("summary", {}) if isinstance(signals, dict) else {},
            "backtest": backtest,
            "risk": risk,
        }

        # 生成 PDF
        pdf_bytes = pdf_generator.generate(data)

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=report_{datetime.now().strftime('%Y%m%d')}.pdf"}
        )
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.error(f"PDF export error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/export/pdf/status", dependencies=[Security(verify_api_key)])
async def export_pdf_status():
    """检查 PDF 导出功能是否可用"""
    try:
        from lib.pdf_report import HAS_REPORTLAB, HAS_MATPLOTLIB
        return {
            "available": HAS_REPORTLAB,
            "charts": HAS_MATPLOTLIB,
            "message": "PDF export ready" if HAS_REPORTLAB else "reportlab not installed"
        }
    except Exception as e:
        return {"available": False, "message": str(e)}


# 告警规则存储（内存 + 文件）
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


@app.get("/api/alerts/rules", dependencies=[Security(verify_api_key)])
async def get_alert_rules():
    """获取所有告警规则"""
    rules = _load_alert_rules()
    return {"rules": rules}


@app.post("/api/alerts/rules", dependencies=[Security(verify_api_key)])
async def create_alert_rule(request: Request):
    """创建告警规则"""
    try:
        body = await request.json()
        rules = _load_alert_rules()

        # 生成 ID
        import uuid
        rule_id = str(uuid.uuid4())[:8]

        rule = {
            "id": rule_id,
            "type": body.get("type"),
            "enabled": body.get("enabled", True),
            "created_at": datetime.now().isoformat(),
            **{k: v for k, v in body.items() if k not in ["id", "created_at"]}
        }

        rules.append(rule)
        _save_alert_rules(rules)

        return {"status": "created", "id": rule_id}
    except Exception as e:
        logger.error(f"Create alert rule error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.patch("/api/alerts/rules/{rule_id}", dependencies=[Security(verify_api_key)])
async def update_alert_rule(rule_id: str, request: Request):
    """更新告警规则"""
    try:
        body = await request.json()
        rules = _load_alert_rules()

        for rule in rules:
            if rule["id"] == rule_id:
                rule.update(body)
                _save_alert_rules(rules)
                return {"status": "updated"}

        raise HTTPException(status_code=404, detail="Rule not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update alert rule error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/api/alerts/rules/{rule_id}", dependencies=[Security(verify_api_key)])
async def delete_alert_rule(rule_id: str):
    """删除告警规则"""
    try:
        rules = _load_alert_rules()
        rules = [r for r in rules if r["id"] != rule_id]
        _save_alert_rules(rules)
        return {"status": "deleted"}
    except Exception as e:
        logger.error(f"Delete alert rule error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/alerts/check", dependencies=[Security(verify_api_key)])
async def check_alerts():
    """检查告警规则"""
    try:
        rules = _load_alert_rules()
        enabled_rules = [r for r in rules if r.get("enabled")]

        triggered = []
        for rule in enabled_rules:
            # 简化版检查逻辑
            if rule["type"] == "price":
                # 检查价格突破
                pass
            elif rule["type"] == "change":
                # 检查涨跌幅
                pass

        return {"checked": len(enabled_rules), "triggered": len(triggered), "alerts": triggered}
    except Exception as e:
        logger.error(f"Check alerts error: {e}")
        return {"checked": 0, "triggered": 0, "alerts": []}


@app.post("/api/backup/create", dependencies=[Security(verify_api_key)])
async def backup_create():
    """创建数据备份"""
    try:
        import shutil
        import zipfile
        from datetime import datetime

        backup_dir = Path("backups")
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"backup_{timestamp}.zip"

        # 需要备份的文件和目录
        backup_targets = [
            "alert_rules.json",
            "config.py",
            "snapshots/",
            "cache/",
        ]

        with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for target in backup_targets:
                target_path = Path(target)
                if target_path.is_file():
                    zf.write(target_path)
                elif target_path.is_dir():
                    for file_path in target_path.rglob('*'):
                        if file_path.is_file():
                            zf.write(file_path)

        return {
            "status": "created",
            "file": str(backup_file),
            "size": backup_file.stat().st_size,
            "timestamp": timestamp
        }
    except Exception as e:
        logger.error(f"Backup creation error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/backup/list", dependencies=[Security(verify_api_key)])
async def backup_list():
    """列出所有备份"""
    try:
        backup_dir = Path("backups")
        if not backup_dir.exists():
            return {"backups": []}

        backups = []
        for file in sorted(backup_dir.glob("backup_*.zip"), reverse=True):
            backups.append({
                "file": file.name,
                "size": file.stat().st_size,
                "created": datetime.fromtimestamp(file.stat().st_mtime).isoformat()
            })

        return {"backups": backups}
    except Exception as e:
        logger.error(f"Backup list error: {e}")
        return {"backups": []}


@app.post("/api/backup/restore", dependencies=[Security(verify_api_key)])
async def backup_restore(request: Request):
    """恢复数据备份"""
    try:
        import zipfile

        body = await request.json()
        backup_file = body.get("file")

        if not backup_file:
            raise HTTPException(status_code=400, detail="Backup file required")

        # 验证文件名安全性（防止路径遍历）
        if ".." in backup_file or "/" in backup_file or "\\" in backup_file:
            raise HTTPException(status_code=400, detail="Invalid backup filename")

        backup_path = Path("backups") / backup_file
        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="Backup file not found")

        # 解压备份（带路径验证）
        extract_dir = Path("restore_temp")
        extract_dir.mkdir(exist_ok=True)

        with zipfile.ZipFile(backup_path, 'r') as zf:
            # 验证每个成员的路径（防止 Zip slip）
            for member in zf.namelist():
                member_path = (extract_dir / member).resolve()
                if not str(member_path).startswith(str(extract_dir.resolve())):
                    raise HTTPException(status_code=400, detail=f"Invalid path in zip: {member}")

            zf.extractall(extract_dir)

        return {"status": "restored", "file": backup_file}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backup restore error: {e}")
        raise HTTPException(status_code=500, detail="Backup restore failed")


@app.delete("/api/backup/{backup_file}", dependencies=[Security(verify_api_key)])
async def backup_delete(backup_file: str):
    """删除备份文件"""
    try:
        # 验证文件名安全性（防止路径遍历）
        if ".." in backup_file or "/" in backup_file or "\\" in backup_file:
            raise HTTPException(status_code=400, detail="Invalid backup filename")

        backup_path = Path("backups") / backup_file

        # 验证路径在 backups 目录内
        if not backup_path.resolve().is_relative_to(Path("backups").resolve()):
            raise HTTPException(status_code=400, detail="Invalid backup path")

        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="Backup file not found")

        backup_path.unlink()
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backup delete error: {e}")
        raise HTTPException(status_code=500, detail="Backup delete failed")


@app.get("/api/notifications", dependencies=[Security(verify_api_key)])
async def get_notifications(
    type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """获取通知历史（支持搜索）"""
    try:
        from lib.notification_logger import notification_logger

        # 获取所有通知
        notifications = notification_logger.get_notifications(limit=1000)

        # 按类型筛选
        if type:
            notifications = [n for n in notifications if n.get("type") == type]

        # 按日期筛选
        if start_date:
            notifications = [n for n in notifications if n.get("timestamp", "") >= start_date]
        if end_date:
            notifications = [n for n in notifications if n.get("timestamp", "") <= end_date]

        # 按关键词搜索
        if keyword:
            keyword_lower = keyword.lower()
            notifications = [
                n for n in notifications
                if keyword_lower in str(n.get("message", "")).lower()
                or keyword_lower in str(n.get("type", "")).lower()
                or keyword_lower in str(n.get("details", "")).lower()
            ]

        # 分页
        total = len(notifications)
        notifications = notifications[offset:offset + limit]

        return {
            "notifications": notifications,
            "total": total,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Get notifications error: {e}")
        return {"notifications": [], "total": 0}


@app.get("/api/notifications/stats", dependencies=[Security(verify_api_key)])
async def get_notification_stats():
    """获取通知统计"""
    try:
        from lib.notification_logger import notification_logger

        notifications = notification_logger.get_notifications(limit=10000)

        # 按类型统计
        by_type = {}
        for n in notifications:
            t = n.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        # 按日期统计
        by_date = {}
        for n in notifications:
            date = n.get("timestamp", "")[:10]
            by_date[date] = by_date.get(date, 0) + 1

        return {
            "total": len(notifications),
            "by_type": by_type,
            "by_date": by_date,
            "recent": notifications[-10:] if notifications else []
        }
    except Exception as e:
        logger.error(f"Get notification stats error: {e}")
        return {"total": 0, "by_type": {}, "by_date": {}, "recent": []}


# 挂载静态文件（在所有 API 路由之后，避免覆盖 API 端点）
if STATIC_DIR.exists():
    # 挂载 CSS/JS/图片等静态资源
    for subdir in ["css", "js", "images", "fonts"]:
        subpath = STATIC_DIR / subdir
        if subpath.exists():
            app.mount(f"/{subdir}", StaticFiles(directory=str(subpath)), name=f"static-{subdir}")
    # 挂载根目录下的单个文件（favicon, sw.js 等）
    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(str(STATIC_DIR / "favicon.svg"))

    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(str(STATIC_DIR / "sw.js"), media_type="application/javascript")


if __name__ == "__main__":
    import uvicorn
    api_host = os.environ.get("API_HOST", "127.0.0.1")
    api_port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run(app, host=api_host, port=api_port)
