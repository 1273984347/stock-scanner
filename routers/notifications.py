"""RF-01 Phase 5 域路由：notifications"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/notifications", dependencies=[Security(verify_api_key)])
async def get_notifications(
    type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """获取通知历史（支持搜索）"""
    try:
        import lib.monitoring.notification_logger as notification_logger
        # 获取所有通知
        notifications = notification_logger.get_notifications(limit=1000)

        # 按类型筛选
        if type:
            notifications = [n for n in notifications if n.get("type") == type]

        # 按日期筛选
        if start_date:
            notifications = [n for n in notifications if n.get("timestamp", "") >= start_date]
        if end_date:
            notifications = [n for n in notifications if n.get("timestamp", "") <= end_date]

        # 按关键词搜索
        if keyword:
            keyword_lower = keyword.lower()
            notifications = [
                n for n in notifications
                if keyword_lower in str(n.get("message", "")).lower()
                or keyword_lower in str(n.get("type", "")).lower()
                or keyword_lower in str(n.get("details", "")).lower()
            ]

        # 分页
        total = len(notifications)
        notifications = notifications[offset:offset + limit]

        return {
            "notifications": notifications,
            "total": total,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Get notifications error: {e}")
        return {"notifications": [], "total": 0}


@router.get("/api/notifications/stats", dependencies=[Security(verify_api_key)])
async def get_notification_stats():
    """获取通知统计"""
    try:
        import lib.monitoring.notification_logger as notification_logger
        notifications = notification_logger.get_notifications(limit=10000)

        # 按类型统计
        by_type = {}
        for n in notifications:
            t = n.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        # 按日期统计
        by_date = {}
        for n in notifications:
            date = n.get("timestamp", "")[:10]
            by_date[date] = by_date.get(date, 0) + 1

        return {
            "total": len(notifications),
            "by_type": by_type,
            "by_date": by_date,
            "recent": notifications[-10:] if notifications else []
        }
    except Exception as e:
        logger.error(f"Get notification stats error: {e}")
        return {"total": 0, "by_type": {}, "by_date": {}, "recent": []}

