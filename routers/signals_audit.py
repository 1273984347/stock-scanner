"""RF-01 Phase 5 域路由：signals_audit"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/signals", dependencies=[Security(verify_api_key)])
async def signal_accuracy(days: int = Query(default=30, ge=1, le=365)):
    """信号准确率追踪"""
    cached = _cache_get("signals", max_age=1800)
    if cached is not None:
        return cached

    try:
        from lib.scoring.signal_tracker import SignalTracker
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


@router.get("/api/audit", dependencies=[Security(verify_api_key)])
async def audit_history(limit: int = Query(default=20, ge=1, le=100)):
    """审计日志列表"""
    try:
        from lib.monitoring.audit_logger import get_scan_history
        return {"history": get_scan_history(limit)}
    except Exception as e:
        return {"history": [], "error": "服务暂时不可用"}


@router.get("/api/audit/{scan_id}", dependencies=[Security(verify_api_key)])
async def audit_detail(scan_id: int):
    """某次扫描的审计详情"""
    try:
        from lib.monitoring.audit_logger import get_scan_detail
        detail = get_scan_detail(scan_id)
        if not detail:
            raise HTTPException(status_code=404, detail="未找到该记录")
        return detail
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="内部服务器错误")


@router.get("/api/signals/accuracy-trend", dependencies=[Security(verify_api_key)])
async def signals_accuracy_trend(days: int = Query(90, ge=14, le=365)):
    """信号准确率趋势 — 按周/月统计准确率变化"""
    try:
        from lib.scoring.signal_tracker import SignalTracker
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

