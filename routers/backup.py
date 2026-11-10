"""RF-01 Phase 5 域路由：backup"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.post("/api/backup/create", dependencies=[Security(verify_api_key)])
async def backup_create():
    """创建数据备份"""
    try:
        import shutil
        import zipfile
        from datetime import datetime

        backup_dir = Path("backups")
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"backup_{timestamp}.zip"

        # 需要备份的文件和目录
        backup_targets = [
            "alert_rules.json",
            "config.py",
            "snapshots/",
            "cache/",
        ]

        with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for target in backup_targets:
                target_path = Path(target)
                if target_path.is_file():
                    zf.write(target_path)
                elif target_path.is_dir():
                    for file_path in target_path.rglob('*'):
                        if file_path.is_file():
                            zf.write(file_path)

        return {
            "status": "created",
            "file": str(backup_file),
            "size": backup_file.stat().st_size,
            "timestamp": timestamp
        }
    except Exception as e:
        logger.error(f"Backup creation error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/backup/list", dependencies=[Security(verify_api_key)])
async def backup_list():
    """列出所有备份"""
    try:
        backup_dir = Path("backups")
        if not backup_dir.exists():
            return {"backups": []}

        backups = []
        for file in sorted(backup_dir.glob("backup_*.zip"), reverse=True):
            backups.append({
                "file": file.name,
                "size": file.stat().st_size,
                "created": datetime.fromtimestamp(file.stat().st_mtime).isoformat()
            })

        return {"backups": backups}
    except Exception as e:
        logger.error(f"Backup list error: {e}")
        return {"backups": []}


@router.post("/api/backup/restore", dependencies=[Security(verify_api_key)])
async def backup_restore(request: Request):
    """恢复数据备份"""
    try:
        import zipfile

        body = await request.json()
        backup_file = body.get("file")

        if not backup_file:
            raise HTTPException(status_code=400, detail="Backup file required")

        # 验证文件名安全性（防止路径遍历）
        if ".." in backup_file or "/" in backup_file or "\\" in backup_file:
            raise HTTPException(status_code=400, detail="Invalid backup filename")

        backup_path = Path("backups") / backup_file
        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="Backup file not found")

        # 解压备份（带路径验证）
        extract_dir = Path("restore_temp")
        extract_dir.mkdir(exist_ok=True)

        with zipfile.ZipFile(backup_path, 'r') as zf:
            # 验证每个成员的路径（防止 Zip slip）
            for member in zf.namelist():
                member_path = (extract_dir / member).resolve()
                if not str(member_path).startswith(str(extract_dir.resolve())):
                    raise HTTPException(status_code=400, detail=f"Invalid path in zip: {member}")

            zf.extractall(extract_dir)

        return {"status": "restored", "file": backup_file}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backup restore error: {e}")
        raise HTTPException(status_code=500, detail="Backup restore failed")


@router.delete("/api/backup/{backup_file}", dependencies=[Security(verify_api_key)])
async def backup_delete(backup_file: str):
    """删除备份文件"""
    try:
        # 验证文件名安全性（防止路径遍历）
        if ".." in backup_file or "/" in backup_file or "\\" in backup_file:
            raise HTTPException(status_code=400, detail="Invalid backup filename")

        backup_path = Path("backups") / backup_file

        # 验证路径在 backups 目录内
        if not backup_path.resolve().is_relative_to(Path("backups").resolve()):
            raise HTTPException(status_code=400, detail="Invalid backup path")

        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="Backup file not found")

        backup_path.unlink()
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backup delete error: {e}")
        raise HTTPException(status_code=500, detail="Backup delete failed")

