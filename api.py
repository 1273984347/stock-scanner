"""RF-01 Phase 5 —— API 装配层（原 api.py）。

仅保留：app 定义、横切中间件/异常/启动钩子、WebSocket、根路由与静态挂载，
以及 17 个域 router 的 include。所有端点逻辑已迁至 routers/ 各域模块。
"""

from routers.common import *  # noqa: F401,F403
from routers import (
    scan,
    scoring,
    stock,
    market,
    portfolio,
    risk,
    factor,
    backtest,
    analysis,
    export,
    settings,
    signals_audit,
    strategy,
    alerts,
    backup,
    notifications,
    system,
)

app = FastAPI(title="A股量化交易系统", version="3.0.0")

@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    return await _http_exception_handler_impl(request, exc)
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


@app.on_event("startup")
def _verify_api_key_on_startup():
    """启动时验证 API 密钥已配置"""
    try:
        _get_api_key()
    except RuntimeError as e:
        logger.error(f"启动失败: {e}")
        raise SystemExit(1)


@app.middleware("http")
async def track_performance(request: Request, call_next):
    """请求性能追踪"""
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    if request.url.path.startswith("/api/"):
        _record_metric(request.url.path, duration, response.status_code)
    return response


@app.on_event("shutdown")
def _flush_perf_on_shutdown():
    """关闭时刷新性能指标"""
    _flush_metrics()


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    return await _add_security_headers(request, call_next)
    """添加安全响应头"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # X-XSS-Protection 显式设为 "0" 禁用旧过滤器（避免引入漏洞），保留头以兼容老扫描器
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
    return response


@app.get("/")
async def index():
    """前端页面"""
    html = STATIC_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return {"message": "A股量化交易系统 API v3", "docs": "/docs"}


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



# ---- 域路由装配（原 api.py 的 68 个端点，按 17 域拆分）----
app.include_router(scan.router)
app.include_router(scoring.router)
app.include_router(stock.router)
app.include_router(market.router)
app.include_router(portfolio.router)
app.include_router(risk.router)
app.include_router(factor.router)
app.include_router(backtest.router)
app.include_router(analysis.router)
app.include_router(export.router)
app.include_router(settings.router)
app.include_router(signals_audit.router)
app.include_router(strategy.router)
app.include_router(alerts.router)
app.include_router(backup.router)
app.include_router(notifications.router)
app.include_router(system.router)
