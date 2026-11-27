#!/usr/bin/env python3
"""blocks 新股覆盖巡检（P-147 防复发）

判据：上市 >= N 自然日的 A 股必须出现在 data/store/blocks。
缺口存在时退出码 1，可直接用于日常巡检 / CI，不需要重跑全量同步。

与 fill_missing_blocks.py 的分工：
- 本脚本**只判不修**（判据与修复分离，避免判据自己改数据）
- 修复走 `py scripts/fill_missing_blocks.py --write`（带行业名白名单校验）

用法：
    py scripts/check_blocks_newstock.py                 # 默认 10 自然日
    py scripts/check_blocks_newstock.py --min-days 5    # 更严格
    py scripts/check_blocks_newstock.py --json          # 机器可读输出
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-days", type=int, default=10, help="上市天数阈值（自然日）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    args = ap.parse_args()

    from lib.data.blocks_health import check_newstock_coverage

    res = check_newstock_coverage(min_listed_days=args.min_days)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"blocks 仓 {res['known_codes']} 只 / 参检新股 {res['checked']} 只"
              f"（上市>={args.min_days} 自然日）")
        if res["ok"]:
            print("[PASS] 新股覆盖：无缺口")
        else:
            print(f"[FAIL] {res['reason']}")
            for item in res["missing"][:30]:
                print(f"  缺 {item['code']}  上市 {item['list_date']}（{item['days_listed']} 天）")
            if len(res["missing"]) > 30:
                print(f"  ... 共 {len(res['missing'])} 只")

    if res["ok"]:
        return 0
    # 判据无法执行（清单不可用）与"有缺口"区分开，避免把取数失败当通过
    return 2 if not res["checked"] and not res["missing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
