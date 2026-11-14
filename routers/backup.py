"""RF-01 Phase 5 域路由：backup"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

def _compute_backup_create() -> dict:
    """同步压缩体（B2-2）：由端点经 `asyncio.to_thread` 放进线程池。

    两处根因修，替代「给端点补一个 run_blocking consumer」：
    * **白名单打包**：旧实现递归整个 `cache/`（api_*.json 响应缓存、push_log、
      user_settings、parquet 行情、ML 模型）做 rglob+DEFLATE，既占住事件循环，
      又把持仓与推送明细外带进 zip（审查报告 P1-6 / P1-8）。
      现在只打包用户配置与快照，压缩规模降到可忽略。
    * **落点不变**：仍用相对 `backups/`，与 list/restore/delete 的既有口径一致，
      响应字段（file 为相对路径）逐字节不变。
    用 `asyncio.to_thread` 而非 `run_blocking`：L9 要求 OFFLOAD_CONSUMERS 严格
    等于 37 个 MIGRATIONS，backup 不是迁移端点，不能塞进灰度消费者集合；
    `asyncio.to_thread` 是 L8 已登记的合法 offload 符号（OFFLOADED_TAILS）。
    """
    import zipfile
    from datetime import datetime

    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"backup_{timestamp}.zip"

    backup_targets = [
        Path("alert_rules.json"),
        Path("config.json"),
        Path("snapshots"),
    ]
    tmp = backup_file.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            for target in backup_targets:
                if target.is_file():
                    zf.write(target)
                elif target.is_dir():
                    for file_path in target.rglob('*'):
                        if file_path.is_file():
                            zf.write(file_path)
        # 原子落地：并发请求不会读到半截 zip，异常时不留 .tmp
        tmp.replace(backup_file)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    return {
        "status": "created",
        "file": str(backup_file),
        "size": backup_file.stat().st_size,
        "timestamp": timestamp
    }


@router.post("/api/backup/create", dependencies=[Security(verify_api_key)])
async def backup_create():
    """创建数据备份（B2-2：白名单 + 线程池，响应形状不变）"""
    try:
        return await asyncio.to_thread(_compute_backup_create)
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

