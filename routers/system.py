"""RF-01 Phase 5 域路由：system"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from lib.infra.api_core import api_key_is_valid

router = APIRouter()

# 批次 A / P1-1：错误上报的字段白名单与体积上界。
# sendBeacon 带不了自定义 header，前端已改为 fetch(keepalive + X-API-Key)，
# 因此这里可以在 handler 内做认证；不挂 Security 依赖是为了保持 L2/L7 的契约形状不变。
_ERROR_REPORT_FIELDS = frozenset({
    "type", "message", "filename", "lineno", "colno", "stack",
    "path", "userAgent", "timestamp",
})
_ERROR_REPORT_MAX_BYTES = 4096
_ERROR_REPORT_MAX_FIELD_LEN = 512

@router.get("/api/health")
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
        cache_dir = PROJECT_ROOT / "cache"  # 批次 A / P2-1：禁止裸 Path(__file__).parent
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


async def _ensure_dashboard_scan(preset: str, orchestrator) -> dict:
    """批次 B4 / P2-6：同一 preset 的并发请求只起一个重扫描任务。

    旧实现 `asyncio.wait_for(asyncio.to_thread(...), timeout=5.0)` 的 to_thread
    线程**不可取消**：超时后重扫描仍在跑，并发 dashboard 各起一个，重复跑多份
    并各自 `_cache_set` 覆盖缓存。现在改为单飞：
    * 在飞任务由 `lib/infra/scan_task_registry` 的带锁 API 唯一持有（T6）；
    * 超时**不 cancel**，任务后台跑完自己写缓存，后续请求等同一个 task；
    * 完成回调只清自己登记的那个 task，避免旧回调误清新任务。
    """
    import asyncio
    from lib.infra.scan_task_registry import (
        get_inflight_scan, set_inflight_scan, clear_inflight_scan,
    )

    key = f"dashboard:{preset}"
    task = get_inflight_scan(key)
    if task is None or task.done():
        async def _run():
            data = await asyncio.to_thread(orchestrator.run_all_strategies)
            _cache_set(f"scan_{preset}", data)
            return data

        task = asyncio.create_task(_run())
        set_inflight_scan(key, task)
        task.add_done_callback(lambda t, k=key: clear_inflight_scan(k, t))

    done, _ = await asyncio.wait({task}, timeout=5.0)
    if not done:
        logger.warning("Dashboard scan 超时 5s，任务后台续跑（单飞）")
        return {}
    try:
        return task.result()
    except Exception as e:
        logger.warning(f"Dashboard 自动触发 scan 失败: {e}")
        return {}


@router.get("/api/dashboard", dependencies=[Security(verify_api_key)])
async def dashboard(preset: str = Query("balanced", description="策略预设")):
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")
    """仪表盘汇总数据（一次请求返回所有仪表盘需要的指标）"""
    cached = _cache_get(dashboard_cache_key(preset), max_age=600)  # 10分钟缓存
    if cached is not None:
        return cached

    try:
        orchestrator = _get_orchestrator(preset)
        # 汇总市场+风控+组合+持仓
        market_data = _cache_get("market", max_age=1800) or {}
        scan_data = _cache_get(f"scan_{preset}", max_age=3600) or {}

        # 缓存为空时主动触发一次 scan（B4 / P2-6：单飞，超时后台续跑不重复起任务）
        if not scan_data:
            scan_data = await _ensure_dashboard_scan(preset, orchestrator)

        # [RF-02 D4-b] 缓存未命中路径残留 4 个阻塞调用（evaluate_macro_gate /
        # get_market_sentiment / np 运算 / _cache_*）。D4-a 的 run_blocking 注册路径被
        # L9 硬门禁 check_consumer_registry 阻断：dashboard 市场块嵌套在 try→
        # `if not market_data` 内，无法满足 check_migration 的 AST 等价判据（即 Phase 6
        # 当初把它转 RF-02 的同一原因）。此处保持同步直调（flag=off 下与现状逐字节等价），
        # 治理以契约快照 + CI 监测覆盖；真正 offload 需扩展 L9 提取机制，列为 RF-02 后续项。
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
        _cache_set(dashboard_cache_key(preset), result)
        return result
    except Exception as e:
        logger.error(f"仪表盘数据失败: {e}")
        raise HTTPException(status_code=500, detail="内部服务器错误")


@router.get("/api/reports", dependencies=[Security(verify_api_key)])
async def report_history(_=Security(verify_api_key)):
    """历史报告列表（来自 cache/reports/）"""
    reports_dir = PROJECT_ROOT / "cache" / "reports"  # 批次 A / P2-1
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


@router.post("/api/errors")
async def report_frontend_error(request: Request):
    """接收前端错误上报（批次 A / P1-1）。

    认证边界：前端已改用 fetch(keepalive + X-API-Key)，未认证直接 401。
    资源上界：body ≤ 4KB + 字段白名单 + 单字段截断，避免无界写盘与日志投毒。
    不挂 Security 依赖是为了保持 L2 契约快照 / L7 归属表的鉴权形状不变；
    认证逻辑复用 api_core.api_key_is_valid，不在此处另写一套比较。
    """
    if not api_key_is_valid(request.headers.get("X-API-Key")):
        raise HTTPException(status_code=401, detail="无效的API密钥")

    body = await request.body()
    if len(body) > _ERROR_REPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="上报内容过大")

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="无效的 JSON 格式")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="上报内容必须是对象")

    clean = {}
    for key in _ERROR_REPORT_FIELDS:
        if key not in data:
            continue
        value = data[key]
        clean[key] = value if isinstance(value, (int, float, bool)) or value is None \
            else str(value)[:_ERROR_REPORT_MAX_FIELD_LEN]

    logger.warning(
        f"Frontend error: {clean.get('type')} - {clean.get('message')} at {clean.get('path')}"
    )

    error_file = PROJECT_ROOT / "cache" / "frontend_errors.jsonl"
    error_file.parent.mkdir(parents=True, exist_ok=True)
    with open(error_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(clean, ensure_ascii=False) + "\n")

    return {"status": "ok"}

