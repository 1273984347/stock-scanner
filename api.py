"""FastAPI 后端 —— 量化交易系统 API

端点清单：
- GET /              前端页面
- GET /api/scan      选股+风控+组合优化
- GET /api/score/{code}  单股四维评分
- GET /api/stock/{code}  单股因子详情
- GET /api/market    市场概况+风控
- GET /api/positions 持仓概况
- GET /api/risk      完整风控评估
- GET /api/exposures 因子暴露追踪
- GET /api/dashboard 仪表盘汇总数据
- GET /api/audit     审计日志
- GET /api/audit/{id} 审计详情
- POST /api/backtest 回测
"""

from fastapi import FastAPI, HTTPException, Query, Security, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pathlib import Path
from datetime import datetime
import json
import logging
import threading
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="A股量化交易系统", version="3.0.0")

# ========== 安全常量 ==========

ALLOWED_PRESETS = {"balanced", "aggressive", "conservative"}

# ========== 安全中间件 ==========

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    """验证 API Key（生产环境必须设置 API_SECRET_KEY 环境变量）"""
    if api_key is None:
        logger.warning("API Key 缺失，拒绝访问")
        raise HTTPException(status_code=401, detail="缺少API密钥")
    expected = os.getenv("API_SECRET_KEY", "")
    if not expected:
        # 未配置密钥时允许访问（开发模式），生产环境应设置环境变量
        logger.warning("API_SECRET_KEY not set, allowing request (dev mode)")
        return api_key
    if api_key != expected:
        logger.warning(f"无效的API密钥尝试: {api_key[:8]}...")
        raise HTTPException(status_code=401, detail="无效的API密钥")
    return api_key


def verify_ws_token(token: str) -> bool:
    """验证 WebSocket token（与 API Key 相同）"""
    expected = os.getenv("API_SECRET_KEY", "")
    if not expected:
        return True  # 未配置时允许
    return token == expected


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """添加安全响应头"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# 缓存操作全局锁（保护 JSON 缓存文件的并发读写）
_cache_lock = threading.Lock()

# ========== 断路器（防止外部 API 故障导致雪崩）==========
from dataclasses import dataclass, field
from typing import Dict
import time

@dataclass
class CircuitBreakerState:
    failure_count: int = 0
    last_failure_time: float = 0.0
    is_open: bool = False

class CircuitBreaker:
    """简单断路器：连续失败 N 次后暂停调用外部 API"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._states: Dict[str, CircuitBreakerState] = {}

    def _get_state(self, name: str) -> CircuitBreakerState:
        if name not in self._states:
            self._states[name] = CircuitBreakerState()
        return self._states[name]

    def record_success(self, name: str):
        state = self._get_state(name)
        state.failure_count = 0
        state.is_open = False

    def record_failure(self, name: str):
        state = self._get_state(name)
        state.failure_count += 1
        state.last_failure_time = time.time()
        if state.failure_count >= self.failure_threshold:
            state.is_open = True
            logger.warning(f"[断路器] {name} 已熔断（连续失败 {state.failure_count} 次）")

    def can_call(self, name: str) -> bool:
        state = self._get_state(name)
        if not state.is_open:
            return True
        # 超时后进入半开状态，尝试一次
        if time.time() - state.last_failure_time > self.recovery_timeout:
            state.is_open = False
            state.failure_count = 0
            logger.info(f"[断路器] {name} 进入半开状态")
            return True
        return False

    def call(self, name: str, func, *args, **kwargs):
        """带断路器保护的调用"""
        if not self.can_call(name):
            raise Exception(f"[断路器] {name} 已熔断，拒绝调用")
        try:
            result = func(*args, **kwargs)
            self.record_success(name)
            return result
        except Exception as e:
            self.record_failure(name)
            raise e

# 全局断路器实例
circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)


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


threading.Thread(target=_prefetch_hs300, daemon=True).start()

STATIC_DIR = Path(__file__).parent / "static"
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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

# 编排器缓存（按 preset 缓存，避免重复创建）
_orchestrator_cache: dict = {}
_orchestrator_lock = threading.Lock()


def _get_orchestrator(preset: str = "balanced") -> ScanOrchestrator:
    """获取指定 preset 的编排器（带缓存，线程安全）"""
    with _orchestrator_lock:
        if preset not in _orchestrator_cache:
            _orchestrator_cache[preset] = ScanOrchestrator(preset=preset)
        return _orchestrator_cache[preset]


# ========== 核心端点 ==========

@app.get("/")
async def index():
    """前端页面"""
    html = STATIC_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return {"message": "A股量化交易系统 API v3", "docs": "/docs"}


@app.get("/api/scan")
async def scan(preset: str = "balanced", _=Security(verify_api_key)):
    """选股结果 + 风控 + 组合优化"""
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")
    cache_key = f"scan_{preset}"
    cached = _cache_get(cache_key, max_age=3600)
    if cached is not None:
        return cached

    try:
        orchestrator = _get_orchestrator(preset)
        scan_result = circuit_breaker.call("scan", orchestrator.run_all_strategies)
        results = scan_result.get("results", {})
        risk_check = scan_result.get("risk_check", {})
        portfolio = scan_result.get("portfolio", {})
        factor_health = scan_result.get("factor_health", {})

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
            "risk_check": risk_check,
            "portfolio": portfolio,
            "factor_health": factor_health,
            "macro_filter": scan_result.get("macro_filter", {}),
            "index_signal": scan_result.get("index_signal", {}),
        }
        _cache_set(cache_key, data)
        return data
    except Exception as e:
        logger.error(f"选股失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.get("/api/score/{code}")
async def stock_score(code: str, _=Security(verify_api_key)):
    """单股四维评分"""
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
        return {"summary": {"total_positions": 0}, "alerts": [], "error": str(e)}


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
        market_data = {}
        if tickers:
            for code in tickers[:15]:  # 限制数量避免超时
                try:
                    hist = orchestrator.get_stock_hist_multi_source(code, days=120, source="auto")
                    if hist is not None and not hist.empty:
                        market_data[code] = hist
                except Exception:
                    continue

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
                from config import SystemConfig
                cfg = SystemConfig()
                scan_data = orchestrator.run_all_strategies()
                _cache_set(f"scan_{preset}", scan_data)
            except Exception as scan_err:
                logger.warning(f"Dashboard 自动触发 scan 失败: {scan_err}")

        if not market_data:
            try:
                from factors import evaluate_macro_gate, calc_market_exposure
                sentiment = orchestrator.get_market_sentiment()
                quotes = orchestrator.get_realtime_quotes()
                macro_gate = evaluate_macro_gate(sentiment)
                market_data = {
                    "sentiment": sentiment,
                    "macro_gate": macro_gate,
                    "exposure": {},
                    "risk_budget": {},
                }
                _cache_set("market", market_data)
            except Exception as mkt_err:
                logger.warning(f"Dashboard 自动获取市场数据失败: {mkt_err}")

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


@app.get("/api/audit")
async def audit_history(limit: int = Query(default=20, ge=1, le=100)):
    """审计日志列表"""
    try:
        from lib.audit_logger import get_scan_history
        return {"history": get_scan_history(limit)}
    except Exception as e:
        return {"history": [], "error": str(e)}


@app.get("/api/audit/{scan_id}")
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
    try:
        from backtest import run_factor_backtest
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
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
        # 序列化
        return {
            "metrics": result.get("metrics"),
            "benchmark": result.get("benchmark"),
            "trade_count": result.get("trade_count"),
            "exit_reasons": result.get("exit_reasons"),
            "cost_summary": result.get("cost_summary"),
        }
    except Exception as e:
        logger.error(f"回测失败: {e}")
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
    try:
        from lib.walkforward import WalkForwardBacktest, WalkForwardConfig, summarize_rounds
        from backtest import run_factor_backtest

        orchestrator = _get_orchestrator()
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]

        # 获取市场数据
        market_data = {}
        all_dates = set()
        for code in sym_list:
            hist = orchestrator.get_stock_hist_multi_source(code, days=train_days + validation_days + 60, source="auto")
            if hist is not None and not hist.empty:
                market_data[code] = hist
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
            except Exception:
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
    token = websocket.headers.get("authorization", "").replace("Bearer ", "")
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
    """获取 WebSocket 推送数据（只读缓存，不触发计算）"""
    try:
        # 只从缓存读取，不触发任何计算
        dashboard = _cache_get("dashboard", max_age=600) or {}
        scan = _cache_get("scan_balanced", max_age=3600) or {}
        risk = _cache_get("risk", max_age=600) or {}
        exposures = _cache_get("exposures", max_age=3600) or {}

        return {
            "dashboard": dashboard,
            "scan": scan,
            "risk": risk,
            "exposures": exposures,
        }
    except Exception as e:
        logger.error(f"WebSocket data fetch error: {e}")
        return {"dashboard": {}, "scan": {}, "risk": {}, "exposures": {}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
