"""RF-01 Phase 5 域路由：alerts"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/alerts/rules", dependencies=[Security(verify_api_key)])
async def get_alert_rules():
    """获取所有告警规则"""
    rules = _load_alert_rules()
    return {"rules": rules}


@router.post("/api/alerts/rules", dependencies=[Security(verify_api_key)])
async def create_alert_rule(request: Request):
    """创建告警规则"""
    try:
        body = await request.json()
        rules = _load_alert_rules()

        # 生成 ID
        import uuid
        rule_id = str(uuid.uuid4())[:8]

        rule = {
            "id": rule_id,
            "type": body.get("type"),
            "enabled": body.get("enabled", True),
            "created_at": datetime.now().isoformat(),
            **{k: v for k, v in body.items() if k not in ["id", "created_at"]}
        }

        rules.append(rule)
        _save_alert_rules(rules)

        return {"status": "created", "id": rule_id}
    except Exception as e:
        logger.error(f"Create alert rule error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/api/alerts/rules/{rule_id}", dependencies=[Security(verify_api_key)])
async def update_alert_rule(rule_id: str, request: Request):
    """更新告警规则"""
    try:
        body = await request.json()
        rules = _load_alert_rules()

        for rule in rules:
            if rule["id"] == rule_id:
                rule.update(body)
                _save_alert_rules(rules)
                return {"status": "updated"}

        raise HTTPException(status_code=404, detail="Rule not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update alert rule error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/api/alerts/rules/{rule_id}", dependencies=[Security(verify_api_key)])
async def delete_alert_rule(rule_id: str):
    """删除告警规则"""
    try:
        rules = _load_alert_rules()
        rules = [r for r in rules if r["id"] != rule_id]
        _save_alert_rules(rules)
        return {"status": "deleted"}
    except Exception as e:
        logger.error(f"Delete alert rule error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/alerts/check", dependencies=[Security(verify_api_key)])
async def check_alerts():
    """检查告警规则"""
    try:
        rules = _load_alert_rules()
        enabled_rules = [r for r in rules if r.get("enabled")]

        triggered = []
        for rule in enabled_rules:
            # 简化版检查逻辑
            if rule["type"] == "price":
                # 检查价格突破
                pass
            elif rule["type"] == "change":
                # 检查涨跌幅
                pass

        return {"checked": len(enabled_rules), "triggered": len(triggered), "alerts": triggered}
    except Exception as e:
        logger.error(f"Check alerts error: {e}")
        return {"checked": 0, "triggered": 0, "alerts": []}

