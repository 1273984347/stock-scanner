#!/usr/bin/env python3
"""blocks 仓差额核对：区分「真缺口 / 过期映射 / 非 A 股 / 退市」

背景（2026-11-27 核对）：2026-11-26 的 blocks 刷新只落了 4,885 只，
旧仓 5,635 个文件里 750 个停留在 2026-11-21。TODOS 原假设是"退市/代码变更后清理"，
本脚本用当日快照与代码前缀验证该假设是否成立。

判据（缺一不可）：
1. **新鲜度按文件 mtime 分区**，不按 manifest 的 dataset updated_at——
   dataset 级更新时间会把"部分文件没刷新"掩盖成"整批已最新"。
2. **仍在快照 = 仍是活标的**：出现在当日 snapshot 的代码，其过期映射是真缺口，不能删。
3. **不在快照的再看代码段**：200xxx / 900xxx 是 B 股（快照只含 A 股，属口径外而非退市）；
   其余才是退市/长停候选，需人工确认。
4. **反向缺口**：快照有、blocks 完全没有的代码（新股常见），单独列出。

输出：cache/blocks_gap_{latest_snapshot_date}.json（gitignored），含四类清单与行业分布。
用法：
    py scripts/audit_blocks_gap.py
    py scripts/audit_blocks_gap.py --blocks data/store/blocks --snapshot data/store/snapshot/2026-11-26.parquet
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _mtime_day(p: Path) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))


def _b_share(code: str) -> bool:
    return code.startswith(("200", "900"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blocks", default=str(ROOT / "data" / "store" / "blocks"))
    ap.add_argument("--snapshot", default="", help="默认取 data/store/snapshot 最新一期")
    ap.add_argument("--out", default="", help="默认 cache/blocks_gap_{snapshot日期}.json")
    args = ap.parse_args()

    import pandas as pd

    blocks_dir = Path(args.blocks)
    files = sorted(blocks_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"[FAIL] blocks 目录为空: {blocks_dir}")

    snap_path = Path(args.snapshot) if args.snapshot else sorted(
        (ROOT / "data" / "store" / "snapshot").glob("*.parquet"))[-1]
    snap_date = snap_path.stem
    snap = pd.read_parquet(snap_path)
    snap["代码"] = snap["代码"].astype(str).str.zfill(6)
    snap = snap.set_index("代码")
    universe = set(snap.index)

    # 1. 按文件 mtime 分区：最新日期 = 本次刷新，其余 = 过期
    days = Counter(_mtime_day(p) for p in files)
    fresh_day = max(days)
    stale_files = [p for p in files if _mtime_day(p) != fresh_day]
    fresh_files = [p for p in files if _mtime_day(p) == fresh_day]

    # 2. 过期文件分三类
    stale_live, stale_b, stale_absent = [], [], []
    for p in stale_files:
        code = p.stem
        name = snap.at[code, "名称"] if code in universe else ""
        try:
            industry = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            industry = None
        row = {"code": code, "name": str(name), "industry": industry, "mtime": _mtime_day(p)}
        if code in universe:
            stale_live.append(row)
        elif _b_share(code):
            stale_b.append(row)
        else:
            stale_absent.append(row)

    # 3. 反向缺口：快照有、blocks 完全没有
    all_codes = {p.stem for p in files}
    missing = [{"code": c, "name": str(snap.at[c, "名称"]), "market": str(snap.at[c, "市场"])}
               for c in sorted(universe - all_codes)]

    # 4. blocks 有、快照没有（B 股 / 退市），仅统计，不处置
    extra = sorted(all_codes - universe)

    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "blocks_dir": str(blocks_dir),
        "snapshot": snap_date,
        "counts": {
            "blocks_files": len(files),
            "fresh_files": len(fresh_files),
            "fresh_day": fresh_day,
            "stale_files": len(stale_files),
            "stale_live_in_snapshot": len(stale_live),
            "stale_b_share": len(stale_b),
            "stale_absent_other": len(stale_absent),
            "snapshot_universe": len(universe),
            "snapshot_without_blocks": len(missing),
            "blocks_not_in_snapshot": len(extra),
        },
        "stale_industry_top": Counter(
            ", ".join(r["industry"]) if isinstance(r["industry"], list) else str(r["industry"])
            for r in stale_live + stale_b + stale_absent
        ).most_common(),
        "stale_live": stale_live,
        "stale_b_share": stale_b,
        "stale_absent_other": stale_absent,
        "snapshot_without_blocks": missing,
        "blocks_not_in_snapshot_sample": extra[:20],
    }

    out = Path(args.out) if args.out else ROOT / "cache" / f"blocks_gap_{snap_date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    c = result["counts"]
    print(f"blocks 文件 {c['blocks_files']}（本次刷新 {c['fresh_files']} / {c['fresh_day']}；"
          f"过期 {c['stale_files']}）")
    print(f"  过期中：仍在快照 {c['stale_live_in_snapshot']} / B 股 {c['stale_b_share']} / "
          f"不在快照且非 B 股 {c['stale_absent_other']}")
    print(f"  反向缺口：快照有但 blocks 完全没有 {c['snapshot_without_blocks']}")
    print(f"  口径外：blocks 有但快照没有 {c['blocks_not_in_snapshot']}（B 股/退市，不处置）")
    print("  过期映射的行业分布（前 8）：")
    for name, n in result["stale_industry_top"][:8]:
        print(f"    {n:5d}  {name}")
    if result["stale_absent_other"]:
        print("  ⚠️ 非 B 股且不在快照，需人工确认退市：")
        for r in result["stale_absent_other"]:
            print(f"    {r['code']}  {r['industry']}")
    print(f"落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
