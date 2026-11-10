"""RF-01 Phase 5 域路由：settings"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/settings", dependencies=[Security(verify_api_key)])
async def get_settings():
    """获取用户设置"""
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return _default_settings()
    except Exception as e:
        logger.error(f"读取设置失败: {e}")
        return _default_settings()


@router.post("/api/settings")
async def save_settings(request: Request, _=Security(verify_api_key)):
    """保存用户设置"""
    try:
        data = await request.json()

        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="设置必须是 JSON 对象")

        # Merge with defaults
        settings = _default_settings()
        settings.update(data)

        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return {"status": "ok", "settings": settings}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

