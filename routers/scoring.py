"""RF-01 Phase 5 域路由：scoring"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from lib.infra.async_offload import run_blocking

router = APIRouter()

def _compute_stock_score(code):
    """原 `GET /api/score/{code}` try 块内容，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    orchestrator = _get_orchestrator()
    result = orchestrator.get_stock_score(code)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/api/score/{code}")
async def stock_score(code: str, _=Security(verify_api_key)):
    """单股四维评分"""
    code = _validate_stock_code(code)
    try:
        return await run_blocking("score", _compute_stock_score, code)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"评分失败 {code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="内部服务器错误")

