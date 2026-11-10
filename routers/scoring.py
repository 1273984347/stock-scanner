"""RF-01 Phase 5 域路由：scoring"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/score/{code}")
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

