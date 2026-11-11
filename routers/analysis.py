"""RF-01 Phase 5 域路由：analysis"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from lib.infra.async_offload import run_blocking

router = APIRouter()

def _compute_return_attribution(days):
    """原 `GET /api/attribution` try 块内容，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    from lib.monitoring.trade_logger import TradeLogger
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


@router.get("/api/attribution", dependencies=[Security(verify_api_key)])
async def return_attribution(days: int = Query(default=30, ge=1, le=365)):
    """收益归因分解（按信号来源瀑布图）"""
    cached = _cache_get("attribution", max_age=3600)
    if cached is not None:
        return cached

    try:
        return await run_blocking("attribution", _compute_return_attribution, days)
    except Exception as e:
        logger.error(f"收益归因失败: {e}")
        return {"attribution": [], "total_return": 0}

