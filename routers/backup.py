"""RF-01 Phase 5 域路由：backup"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

# 批次 C / P1-7：恢复 ZIP 的硬上限（旧实现完全没有任何上限）
MAX_RESTORE_MEMBERS = 200_000
MAX_RESTORE_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB

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

    # P1-3（2026-11-20）：白名单补 4 类**不可再生**资产——此前只含可再生的配置类，
    # 「跑过备份」也无法恢复信号追踪史 / 模拟盘权益 / 决策审计 / ML 模型：
    #   cache/signals/signal_history.json（60 天信号验证史）
    #   cache/paper_trading/（模拟盘权益曲线与交易流水）
    #   cache/audit.db（+WAL/-shm 侧车，决策审计；缺省文件自动跳过）
    #   cache/models/（ML 模型，丢失后 MLPredictor 无模型可加载）
    # api_*.json / parquet 行情缓存等可再生数据仍不进备份（P1-8 口径不变：
    # 本 zip 只落本地 backups/，含模拟盘持仓明细属预期，端点有鉴权）。
    backup_targets = [
        Path("alert_rules.json"),
        Path("config.json"),
        Path("snapshots"),
        Path("cache/signals/signal_history.json"),
        Path("cache/paper_trading"),
        Path("cache/audit.db"),
        Path("cache/audit.db-wal"),
        Path("cache/audit.db-shm"),
        Path("cache/models"),
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


def _compute_backup_restore(backup_path: Path, extract_dir: Path, backup_file: str) -> dict:
    """同步恢复体（批次 C / P1-7），经 `asyncio.to_thread` 放进线程池。

    旧实现用字符串前缀比较 `str(member_path).startswith(str(root))`，
    `../restore_temp_evil/x` 这类兄弟目录会误放行（同文件 delete 接口早已用
    `is_relative_to`）。现在：逐成员 resolve 后二次校验 + 成员数/解压总字节上限，
    并且不再用 `extractall`（它会自行建目录、绕过逐成员校验）。
    逐成员写入必然多出 open/mkdir 等阻塞调用，所以整段搬进线程池 ——
    L8 记 blocking 降、offloaded 升，方向与判据一致，不需要放宽门禁。
    """
    import shutil
    import zipfile

    root = extract_dir.resolve()
    with zipfile.ZipFile(backup_path, 'r') as zf:
        infos = zf.infolist()
        if len(infos) > MAX_RESTORE_MEMBERS:
            raise HTTPException(status_code=400, detail="Backup has too many entries")
        declared = sum(i.file_size for i in infos)
        if declared > MAX_RESTORE_BYTES:
            raise HTTPException(status_code=400, detail="Backup too large to restore")

        for info in infos:
            target = (extract_dir / info.filename).resolve()
            if not target.is_relative_to(root):
                raise HTTPException(status_code=400,
                                    detail=f"Invalid path in zip: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    return {"status": "restored", "file": backup_file}


@router.post("/api/backup/restore", dependencies=[Security(verify_api_key)])
async def backup_restore(request: Request):
    """恢复数据备份"""
    try:
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

        extract_dir = Path("restore_temp")
        extract_dir.mkdir(exist_ok=True)
        return await asyncio.to_thread(
            _compute_backup_restore, backup_path, extract_dir, backup_file)
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

