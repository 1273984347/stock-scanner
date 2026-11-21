"""日志清理 —— 追加型日志的保留期治理（P1-3 零星项，批次 C12，2026-11-20）

覆盖两类**永不清理**的追加日志（评审 P1-3 / TODO 批次 C7 遗留）：
- ``logs/paper_daily_*.log``（paper_daily.bat 按天分文件、永不清理）
- ``logs/scheduled/*.log``（bootstrap_schedules.ps1 注册任务的 stdout 重定向）

保留期默认 30 天；按文件 mtime 判定。只删匹配模式的日志文件，
其它任何文件不受影响。可独立运行，也被 bootstrap_schedules.ps1 的
TdxUpdate 任务链式调用（更新完成顺手清日志）。

用法：``python scripts/cleanup_logs.py [--days 30] [--dry-run]``
"""
import argparse
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RETENTION_DAYS = 30
# 相对仓库根的 glob 模式（只收追加型日志，不动 JSONL/DB 等有 rotation 的资产）
PATTERNS = ("logs/paper_daily_*.log", "logs/scheduled/*.log")


def cleanup(root: Path = ROOT, retention_days: int = RETENTION_DAYS,
            dry_run: bool = False) -> list:
    """删除超保留期的日志文件，返回被删（或将被删）的路径列表。"""
    cutoff = time.time() - retention_days * 86400
    removed: list = []
    for pat in PATTERNS:
        for f in sorted(root.glob(pat)):
            try:
                if f.stat().st_mtime >= cutoff:
                    continue
                if not dry_run:
                    f.unlink()
                removed.append(str(f.relative_to(root)))
            except OSError as e:
                print(f"[cleanup] 跳过 {f}: {e}")
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="清理超保留期的追加日志")
    ap.add_argument("--days", type=int, default=RETENTION_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    removed = cleanup(ROOT, args.days, args.dry_run)
    tag = "(dry-run)" if args.dry_run else ""
    print(f"[cleanup] 保留 {args.days} 天：{len(removed)} 个文件{' 待删' if args.dry_run else ' 已删'} {tag}")
    for r in removed:
        print(f"  - {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
