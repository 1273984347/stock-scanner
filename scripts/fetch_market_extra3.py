"""补充第三批：筹码分布 chip（A股批量）+ 分时 minute（逐只）。

chip: 20只/批，历史区间 2026-11-01~11-24
minute: 单只，仅近1日（支持 --days 5）

用法：
    python scripts/fetch_market_extra3.py --mode chip
    python scripts/fetch_market_extra3.py --mode minute --limit 100
"""
import argparse
import json
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from bulk_paths import RAW, UNIVERSE, REPORTS  # noqa: E402
from lib.data.data_westock import _parse_markdown_table  # noqa: E402
from fetch_market_bulk import OUT, run_cli  # noqa: E402

EXTRA = OUT / "_extra3"
LOGDIR = EXTRA / "_logs"
for d in (EXTRA, LOGDIR):
    d.mkdir(parents=True, exist_ok=True)


def fetch_chip(sleep=0.5):
    """逐只拉单日筹码快照（带 code 列，无批量归属歧义）。5580 只约 10 分钟。"""
    codes = [c.strip() for c in (UNIVERSE / "universe_hs.txt").read_text(encoding="utf-8").splitlines() if c.strip()]
    log = LOGDIR / "chip.jsonl"
    done = set()
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line).get("_code"))
            except Exception:
                pass
    todo = [c for c in codes if c not in done]
    print(f"[chip] total={len(codes)} done={len(done)} todo={len(todo)}", flush=True)
    ok = fail = 0
    for i, c in enumerate(todo):
        out, rc = run_cli(["chip", c, "--date", "2026-11-24"], timeout=30, retries=1)
        df = _parse_markdown_table(out)
        if not df.empty:
            with log.open("a", encoding="utf-8") as f:
                for _, row in df.iterrows():
                    r = row.to_dict()
                    r["_code"] = c
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            ok += 1
        else:
            fail += 1
        if (i + 1) % 100 == 0 or i + 1 == len(todo):
            print(f"  {i+1}/{len(todo)} ok={ok} fail={fail}", flush=True)
        time.sleep(sleep)


def fetch_minute(limit=0, sleep=1.0):
    """minute 无批量：逐只拉。全市场 16152 只约 4-5 小时。"""
    log = LOGDIR / "minute.jsonl"
    done = set()
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line).get("_code"))
            except Exception:
                pass
    files = [("hs", UNIVERSE / "universe_hs.txt"), ("hk", UNIVERSE / "universe_hk.txt"), ("us", UNIVERSE / "universe_us.txt")]
    all_codes = []
    for m, p in files:
        all_codes += [(m, c.strip()) for c in p.read_text(encoding="utf-8").splitlines() if c.strip() and c.strip() not in done]
    if limit:
        all_codes = all_codes[:limit]
    print(f"[minute] todo={len(all_codes)}", flush=True)
    ok = fail = 0
    for i, (m, c) in enumerate(all_codes):
        out, rc = run_cli(["minute", c, "--days", "1"], timeout=30, retries=1)
        df = _parse_markdown_table(out)
        if not df.empty:
            with log.open("a", encoding="utf-8") as f:
                for _, row in df.iterrows():
                    r = row.to_dict()
                    r["_code"] = c
                    r["_market"] = m
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            ok += 1
        else:
            fail += 1
        if (i + 1) % 100 == 0 or i + 1 == len(all_codes):
            print(f"  {i+1}/{len(all_codes)} ok={ok} fail={fail}", flush=True)
        time.sleep(sleep)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["chip", "minute"], default="chip")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()
    if args.mode == "chip":
        fetch_chip(args.sleep)
    else:
        fetch_minute(args.limit, args.sleep)
