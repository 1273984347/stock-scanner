"""行业周期数据探针 —— 验证 G0 闸门

用法:
    python scripts/probe_industry_cycle.py --blocks 5

输出:
    cache/industry_probe_YYYYMMDD_HHMMSS.json（每块 pass 字段）
    退出码 0 = 全部块数据可获取；1 = 部分失败（看 JSON 判断）；2 = 脚本异常

G0 通过条件（由执行器判定，脚本只输出事实）：
    pass=True 的块数 ≥ 3
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

REQUIRED_COLS = {"inventory_yoy", "revenue_yoy", "capex_yoy", "debt_yoy"}
MIN_QUARTERS = 32          # 与 CycleWarningConfig.min_obs 一致
MIN_SPAN_YEARS = 8         # 数据跨度下限


def probe_one(block: str) -> dict:
    from lib.data_industry import fetch_block_financial_series
    df = fetch_block_financial_series(block)
    if df is None or df.empty:
        return {"block": block, "error": "no_data", "pass": False}
    cols_ok = REQUIRED_COLS.issubset(set(df.columns))
    n = int(len(df))
    span = 0.0
    if n >= 2:
        dates = pd.to_datetime(df["date"])
        span = (dates.max() - dates.min()).days / 365.25
    return {
        "block": block,
        "columns_ok": bool(cols_ok),
        "n_quarters": n,
        "span_years": round(float(span), 2),
        "pass": bool(cols_ok and n >= MIN_QUARTERS and span >= MIN_SPAN_YEARS),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocks", type=int, default=5)
    args = parser.parse_args()

    from lib.data_layer import get_all_blocks
    blocks = get_all_blocks()
    candidates = [b for b, codes in blocks.items() if len(codes) >= 20]
    candidates.sort(key=lambda b: len(blocks[b]), reverse=True)
    targets = candidates[: args.blocks]

    results = []
    for i, block in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] probing {block} ({len(blocks[block])} stocks) ...")
        try:
            results.append(probe_one(block))
        except Exception as e:
            results.append({"block": block, "error": repr(e), "pass": False})

    out_dir = Path("cache")
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"industry_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    print(f"probe result: {sum(1 for r in results if r.get('pass'))}/{len(results)} blocks PASS -> {out}")
    return 0 if all(r.get("pass") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
