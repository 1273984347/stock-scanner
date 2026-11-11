"""RF-01 Phase 5 域路由：stock"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from lib.infra.async_offload import run_blocking

router = APIRouter()

def _compute_stock_detail(code):
    """原 `GET /api/stock/{code}` 校验守卫之后的函数体，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    from factors import compute_all_signals
    from lib.scoring.technical_signals import calc_composite_verdict

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


@router.get("/api/stock/{code}")
async def stock_detail(code: str, _=Security(verify_api_key)):
    """单股因子详情"""
    code = _validate_stock_code(code)
    return await run_blocking("stock", _compute_stock_detail, code)

