"""合并 repair 补拉数据到主 parquet + 全市场覆盖对账总表。

repair 日志（_repair_logs/*.jsonl）逐行是补拉的代码数据；主分片是
market_category_*.parquet。本脚本：
1. 把每个类别的 repair 日志读成 DataFrame，单独存 market_category_repair.parquet
   （不改动原分片，保持可追溯）
2. 逐 (market, category) 做 universe - 实际覆盖 对账，输出 coverage_report.csv
3. 数据源真实无覆盖的代码单列 unavailable_codes.csv，标注为合理缺口
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from bulk_paths import RAW, UNIVERSE, REPORTS  # noqa: E402


OUT = ROOT / "data" / "exports" / "bulk"
LOG_DIR = OUT / "_repair_logs"
REPORT = REPORTS / "coverage_report.csv"
UNAVAILABLE = REPORTS / "unavailable_codes.csv"


def main():
    rows = []
    unavailable = []
    for m in ("hs", "hk", "us"):
        universe_file = UNIVERSE / f"universe_{m}.txt"
        if not universe_file.exists():
            continue
        universe = set(universe_file.read_text(encoding="utf-8").split())
        for c in ("quote", "kline", "finance", "technical"):
            # 主分片覆盖
            got = set()
            total_rows = 0
            for f in OUT.glob(f"{m}_{c}_*.parquet"):
                df = pd.read_parquet(f)
                total_rows += len(df)
                if "code" in df.columns:
                    got.update(df["code"].dropna().tolist())
            # repair 分片：合并已有 repair.parquet（旧版）与 jsonl（新版）
            repair_rows = 0
            rep_parquet = OUT / f"{m}_{c}_repair.parquet"
            if rep_parquet.exists():
                df = pd.read_parquet(rep_parquet)
                if "code" in df.columns:
                    got.update(df["code"].dropna().tolist())
                repair_rows += len(df)
            log = LOG_DIR / f"{m}_{c}.jsonl"
            failed_codes = set()
            if log.exists():
                parsed = []
                for line in log.read_text(encoding="utf-8").splitlines():
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("ok") is False:
                        failed_codes.add(r.get("code"))
                        continue
                    parsed.append(r)
                    if r.get("code"):
                        got.add(r["code"])
                if parsed:
                    rp = OUT / f"{m}_{c}_repair.parquet"
                    pd.DataFrame(parsed).to_parquet(rp, index=False)
                    repair_rows += len(parsed)
            missing = universe - got
            # technical 表无 code 列，覆盖度用批次数近似，不列 unavailable
            if c != "technical":
                for code in sorted(missing):
                    unavailable.append({"market": m, "category": c, "code": code,
                                        "reason": "data_source_no_coverage"})
            rows.append({
                "market": m, "category": c,
                "universe": len(universe), "covered": len(got),
                "missing": len(missing), "coverage_pct": round(len(got)/len(universe)*100, 2),
                "main_rows": total_rows, "repair_rows": repair_rows,
                "total_rows": total_rows + repair_rows,
                "failed_fetch": len(failed_codes),
            })
    df = pd.DataFrame(rows)
    df.to_csv(REPORT, index=False, encoding="utf-8-sig")
    pd.DataFrame(unavailable).to_csv(UNAVAILABLE, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False))
    print(f"\n报告: {REPORT}")
    print(f"无覆盖清单: {UNAVAILABLE} ({len(unavailable)} 条)")


if __name__ == "__main__":
    main()
