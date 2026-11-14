"""RF-01 Phase 5 —— API 装配层（原 api.py）。

仅保留：app 定义、横切中间件/异常/启动钩子、WebSocket、根路由与静态挂载，
以及 17 个域 router 的 include。所有端点逻辑已迁至 routers/ 各域模块。
"""

import base64
import os
import uuid

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

# 批次 C / P2-8：文档端点按环境开关，默认保持开启以维持既有 72 条路由契约。
# 生产部署应设 `ENABLE_API_DOCS=0`：否则未认证 `GET /openapi.json` 就是全部端点的攻击地图。
_DOCS_ON = os.environ.get("ENABLE_API_DOCS", "1").strip().lower() not in ("0", "false", "no")

app = FastAPI(
    title="A股量化交易系统",
    version="3.0.0",
    docs_url="/docs" if _DOCS_ON else None,
    redoc_url="/redoc" if _DOCS_ON else None,
    openapi_url="/openapi.json" if _DOCS_ON else None,
)

@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    return await _http_exception_handler_impl(request, exc)


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """P1-3 修复：处理所有未捕获的异常（漏网之鱼），避免空白 500。

    批次 C / P2-7：不再把 `str(exc)` 回显给客户端（内部路径/依赖/配置片段会泄露）。
    改为固定文案 + trace_id，详情只进服务端日志，便于按 id 关联排查。
    """
    trace_id = uuid.uuid4().hex[:12]
    logger.error(
        f"API 未处理异常 [{trace_id}]: {request.method} {request.url.path} -> "
        f"{type(exc).__name__}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "内部服务器错误", "trace_id": trace_id},
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


@app.get("/")
async def index():
    """前端页面"""
    html = STATIC_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return {"message": "A股量化交易系统 API v3", "docs": "/docs"}


def _ws_credentials(websocket) -> tuple[str, str | None]:
    """批次 C / P1-2：只从握手头取 WS 凭据，**不再读 URL query**。

    1. `Authorization: Bearer <key>` —— 原生客户端；
    2. `Sec-WebSocket-Protocol: bearer.<base64url(key)>` —— 浏览器 WebSocket API
       不能设自定义头，子协议是唯一可携带凭证的头。base64url 让任意 key 都落在
       子协议合法字符集内（RFC 6455 token），且 key 不会出现在 URL / access log /
       Referer 里。

    返回 (token, 需要回显给客户端的子协议或 None)。
    """
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[len("Bearer "):].strip(), None
    proto = websocket.headers.get("sec-websocket-protocol", "")
    for part in (p.strip() for p in proto.split(",")):
        if part.startswith("bearer."):
            raw = part[len("bearer."):]
            try:
                padded = raw + "=" * (-len(raw) % 4)
                return base64.urlsafe_b64decode(padded.encode()).decode(), part
            except Exception:
                return "", None
    return "", None


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # S3: WebSocket 认证（P1-2 起仅走握手头）
    token, subproto = _ws_credentials(websocket)
    if not verify_ws_token(token):
        await websocket.close(code=4001)
        return
    if not await manager.connect(websocket, subprotocol=subproto):
        return  # 连接被拒绝
    try:
        # 连接建立时立即推送一次全量数据。
        # 刻意保留 _get_ws_data 的端点内调用点：L8 通过它展开整条阻塞链，
        # 若把构造全挪进后台任务，B1 刚修好的门禁视野又会退化成盲区。
        await websocket.send_json({
            "type": "data_update",
            "timestamp": datetime.now().isoformat(),
            "data": await _get_ws_data()
        })

        # 定时推送由 _ws_broadcast_loop 单后台任务统一构造+广播（B2-1 / P1-3）。
        # 本端点只保持连接，直到客户端断开触发 WebSocketDisconnect。
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


@app.on_event("startup")
async def _start_ws_broadcast():
    """单后台广播任务，进程内唯一（B2-1）。"""
    app.state.ws_broadcast_task = asyncio.create_task(_ws_broadcast_loop())


@app.on_event("shutdown")
async def _stop_ws_broadcast():
    task = getattr(app.state, "ws_broadcast_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


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
