"""补真正的前复权 K线（A股全量 × 500 条，腾讯源 qfq）。

背景（P-131）：Go CLI 的 --fq qfq 不生效（与不复权逐值相同），hfq 反推与
腾讯基准中位差 1.19% 不可用。本脚本改用项目已对拍的腾讯源
lib.data.data_kline_web.get_tencent_kline(adjust='qfq')，单次上限 500 条。

逐只落 jsonl 断点续传；失败记录跳过，重跑自动补。

用法：
    python scripts/fetch_qfq_kline.py
    python scripts/fetch_qfq_kline.py --limit-codes 20   # 试跑
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from bulk_paths import RAW, UNIVERSE, REPORTS  # noqa: E402
from lib.data.data_kline_web import get_tencent_kline  # noqa: E402
from fetch_market_bulk import OUT  # noqa: E402

LOG_DIR = OUT / "_qfq"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG = LOG_DIR / "hs_qfq.jsonl"
FAILED = LOG_DIR / "hs_qfq_failed.jsonl"


def done_codes() -> set:
    if not LOG.exists():
        return set()
    done = set()
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            done.add(r["code"])
        except Exception:
            pass
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=500)
    ap.add_argument("--limit-codes", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    universe = [c.strip() for c in (UNIVERSE / "universe_hs.txt").read_text(encoding="utf-8").splitlines() if c.strip()]
    done = done_codes()
    todo = [c for c in universe if c not in done]
    if args.limit_codes:
        todo = todo[:args.limit_codes]
    print(f"[qfq] universe={len(universe)} done={len(done)} todo={len(todo)}", flush=True)
    ok = fail = 0
    for i, wc in enumerate(todo):
        code = wc[2:] if wc.startswith(("sh", "sz", "bj")) else wc  # 腾讯源用 6 位裸码
        try:
            df = get_tencent_kline(code, days=args.days, adjust="qfq")
            if df is not None and not df.empty:
                with LOG.open("a", encoding="utf-8") as f:
                    for _, row in df.iterrows():
                        rec = row.to_dict()
                        rec["code"] = wc
                        if "date" in rec and hasattr(rec["date"], "isoformat"):
                            rec["date"] = rec["date"].isoformat()[:10]
                        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                ok += 1
            else:
                fail += 1
                with FAILED.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"code": wc, "reason": "empty"}, ensure_ascii=False) + "\n")
        except Exception as e:
            fail += 1
            with FAILED.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"code": wc, "reason": f"{type(e).__name__}: {e}"[:120]},
                                   ensure_ascii=False) + "\n")
        if (i + 1) % 50 == 0 or i + 1 == len(todo):
            print(f"  {i+1}/{len(todo)} ok={ok} fail={fail}", flush=True)
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()
