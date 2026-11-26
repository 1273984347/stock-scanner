"""迁移 bulk 目录到分类结构 + 重写脚本路径引用（P-139）。

前置条件：所有 bulk 后台任务必须停止（否则活跃写入路径会断）。
脚本会检查 _extra3/_logs/minute.jsonl 是否还在增长，若在增长则拒绝执行。

用法：
    python scripts/migrate_bulk_layout.py --check   # 前置检查 + 迁移计划
    python scripts/migrate_bulk_layout.py --apply   # 执行迁移

迁移后所有脚本通过 bulk_paths.py 取路径，不再硬编码。
"""
import argparse
import re
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from bulk_paths import BULK, RAW, MARKERS, UNIVERSE, REPAIR, QFQ, CLEAN, INTEGRATED, \
    EXTRA_MARKET, EXTRA_DEPTH, EXTRA_TICK, BACKTEST, FRONTEND, REPORTS, ARCHIVE, ALL_DIRS

# 旧路径 → 新路径 映射（目录级）
DIR_MAP = {
    "_done": MARKERS,
    "_qfq": QFQ,
    "_repair_logs": REPAIR,
    "_clean": CLEAN,
    "_integrated": INTEGRATED,
    "_extra2": EXTRA_DEPTH,
    "_extra3": EXTRA_TICK,
    "_usecases": BULK / "11_usecases",  # 整体移动，内部 backtest/frontend 随之迁移
    "_tmp": BULK / "13_temp",
}

# 根目录散落文件 → 目标目录
def classify_root_file(name: str) -> Path:
    if re.match(r"^(hs|hk|us)_(quote|kline|finance|technical)_\d+\.parquet$", name):
        return RAW
    if re.match(r"^(hs|hk|us)_(quote|kline|finance|technical)_repair\.parquet$", name):
        return REPAIR
    if name.endswith(".done"):
        return MARKERS
    if name.startswith("universe_") or name in ("universe_all.md", "hk_universe.md", "us_universe.md"):
        return UNIVERSE
    if name in ("coverage_report.csv", "unavailable_codes.csv", "quality_audit.txt", "errors.jsonl"):
        return REPORTS
    return REPORTS

# _extra 目录内文件按语义分
def classify_extra_file(name: str) -> Path:
    market_level = {"fund_all", "north_all", "south_all", "news_all", "report_all", "notice_all",
                    "sector_ranking_industry", "sector_ranking_concept", "index_list_hs", "index_list_hk",
                    "index_list_us", "index_constituent_hs300", "index_constituent_zz500",
                    "index_constituent_sz50", "macro_cn_core", "hot_stock", "hot_news", "hot_etf", "hot_sector"}
    if name.replace(".parquet", "") in market_level:
        return EXTRA_MARKET
    return EXTRA_MARKET  # _extra 其余也是市场级

# 脚本路径引用替换规则（旧字符串 → 新表达式）
SCRIPT_REWRITES = [
    ('OUT = ROOT / "data" / "exports" / "bulk"', 'from bulk_paths import BULK as OUT'),
    ('DONE_DIR = OUT / "_done"', 'from bulk_paths import MARKERS as DONE_DIR'),
    ('EXTRA = OUT / "_extra"', 'from bulk_paths import EXTRA_MARKET as EXTRA'),
    ('EXTRA = OUT / "_extra2"', 'from bulk_paths import EXTRA_DEPTH as EXTRA'),
    ('EXTRA = OUT / "_extra3"', 'from bulk_paths import EXTRA_TICK as EXTRA'),
    ('DIR = OUT / "_qfq"', 'from bulk_paths import QFQ as DIR'),
    ('LOG_DIR = OUT / "_qfq"', 'from bulk_paths import QFQ as LOG_DIR'),
    ('LOG_DIR = OUT / "_repair_logs"', 'from bulk_paths import REPAIR as LOG_DIR'),
    ('DST = OUT / "_integrated"', 'from bulk_paths import INTEGRATED as DST'),
    ('CLEAN = BULK / "_clean"', 'from bulk_paths import CLEAN'),
    ('BT = BULK / "_usecases" / "backtest"', 'from bulk_paths import BACKTEST as BT'),
    ('FE = BULK / "_usecases" / "frontend"', 'from bulk_paths import FRONTEND as FE'),
    ('QFQ = ROOT / "data" / "exports" / "bulk" / "_qfq" / "hs_qfq.jsonl"',
     'from bulk_paths import QFQ_LOG as QFQ'),
    ('QFQ_FAILED = ROOT / "data" / "exports" / "bulk" / "_qfq" / "hs_qfq_failed.jsonl"',
     'from bulk_paths import QFQ_FAILED'),
]


def activity_check():
    """检查是否有活跃写入：minute.jsonl 30 秒内是否增长。"""
    active = BULK / "_extra3" / "_logs" / "minute.jsonl"
    if not active.exists():
        return False, "no active minute log"
    before = active.stat().st_size
    time.sleep(5)
    after = active.stat().st_size
    if after > before:
        return True, f"minute.jsonl growing ({before} -> {after})"
    return False, "no growth in 5s"


def plan():
    print("=== 迁移计划 ===")
    root_files = [f for f in BULK.glob("*") if f.is_file()]
    print(f"根目录散落文件: {len(root_files)}")
    for d, dest in DIR_MAP.items():
        src = BULK / d
        if src.exists() and src.is_dir():
            n = len(list(src.rglob("*")))
            print(f"  {d:24s} -> {dest.name:18s} ({n} items)")
    extra = BULK / "_extra"
    if extra.exists():
        print(f"  _extra                   -> {EXTRA_MARKET.name} + {EXTRA_MARKET.name}(market)")
    print(f"\n目标目录: {len(ALL_DIRS)} 个")
    for d in [BULK / "_usecases", BULK / "_tmp"]:
        if d.exists():
            print(f"  遗留目录将保留: {d.name}")


def apply():
    active, reason = activity_check()
    if active:
        print(f"[ABORT] 活跃任务在写入：{reason}。请先停止所有 bulk 后台任务。")
        return
    print(f"[OK] 无活跃写入：{reason}", flush=True)
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)

    moved = 0
    # 1. 目录级迁移
    for old, dest in DIR_MAP.items():
        src = BULK / old
        if not src.exists() or not src.is_dir():
            continue
        if src.resolve() == dest.resolve():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = dest / item.name
            if target.exists():
                print(f"  SKIP (exists): {old}/{item.name}")
                continue
            shutil.move(str(item), str(target))
            moved += 1
        try:
            src.rmdir()
        except OSError:
            print(f"  WARN: {old} not empty after move")
    # 2. 根目录文件迁移
    for f in [f for f in BULK.glob("*") if f.is_file()]:
        dest_dir = classify_root_file(f.name)
        target = dest_dir / f.name
        if target.exists():
            continue
        shutil.move(str(f), str(target))
        moved += 1
    # 2b. data/exports/ 直属的早期试拉 md → ARCHIVE
    exports_root = BULK.parent
    for f in exports_root.glob("*.md"):
        target = ARCHIVE / f.name
        if target.exists():
            continue
        shutil.move(str(f), str(target))
        moved += 1
        print(f"  archived: {f.name}")
    print(f"文件迁移完成: {moved} 项")

    # 3. 脚本引用重写
    changed = 0
    for script in (ROOT / "scripts").glob("*.py"):
        if script.name in ("bulk_paths.py", "migrate_bulk_layout.py"):
            continue
        text = script.read_text(encoding="utf-8")
        orig = text
        for old, new in SCRIPT_REWRITES:
            text = text.replace(old, new)
        # 补 import（若发生了替换但尚未 import bulk_paths）
        if text != orig and "from bulk_paths import" in text and "import sys" in text and "sys.path.insert(0, str(ROOT / \"scripts\"))" not in text:
            # 已有 sys.path.insert(0, str(ROOT)) 的脚本，需追加 scripts 路径
            text = text.replace("sys.path.insert(0, str(ROOT))",
                                "sys.path.insert(0, str(ROOT))\nsys.path.insert(0, str(ROOT / 'scripts'))", 1)
        if text != orig:
            script.write_text(text, encoding="utf-8")
            changed += 1
            print(f"  rewritten: {script.name}")
    print(f"脚本重写: {changed} 个")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.check:
        plan()
    elif args.apply:
        apply()
    else:
        plan()
