"""RF-01 Phase 5 域路由：scan"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/scan")
async def scan(preset: str = "balanced", _=Security(verify_api_key)):
    """选股结果 — 立即返回缓存，无缓存时异步启动扫描"""
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")

    cache_key = f"scan_{preset}"
    cached = _cache_get(cache_key, max_age=3600)
    if cached is not None:
        return cached

    # 无缓存：检查是否有扫描在运行
    task = get_task(preset)
    if task and task["status"] == "running":
        elapsed = int(time.time() - task["started_at"])
        return {"preset": preset, "results": {}, "total": 0, "scanning": True, "elapsed_sec": elapsed}

    # 启动后台扫描
    t = threading.Thread(target=_run_scan_background, args=(preset,), daemon=True)
    t.start()
    return {"preset": preset, "results": {}, "total": 0, "scanning": True, "elapsed_sec": 0}


@router.get("/api/scan/status")
async def scan_status(preset: str = "balanced"):
    """扫描状态查询"""
    task = get_task(preset)
    if not task:
        return {"preset": preset, "status": "idle"}
    elapsed = int(time.time() - task["started_at"])
    return {"preset": preset, "status": task["status"], "elapsed_sec": elapsed, "error": task.get("error")}

