"""前复权 K线低速重试（补 P-133 WAF 封禁造成的 4530 只缺口）。

策略（防 WAF）：
- 每只间隔 5s，每 50 只冷却 30s
- 检测 WAF（连续空结果）→ 退避 300s 再继续，不记失败
- 区分「WAF 封禁」与「真无数据」：先用已知有效码 600519 探活
- 断点续传：跳过 _qfq.jsonl 中已完成的 code，从 failed 清单读待办

用法：
    python scripts/fetch_qfq_retry_slow.py
    python scripts/fetch_qfq_retry_slow.py --limit 20   # 试跑
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.data.data_kline_web import get_tencent_kline  # noqa: E402
from fetch_market_bulk import OUT  # noqa: E402

DIR = OUT / "_qfq"
LOG = DIR / "hs_qfq.jsonl"
FAILED = DIR / "hs_qfq_failed.jsonl"
PROBE = "600519"  # 已知有效码，用于区分 WAF 封禁 vs 单票无数据


def load_done() -> set:
    if not LOG.exists():
        return set()
    done = set()
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            done.add(json.loads(line)["code"])
        except Exception:
            pass
    return done


def is_waf() -> bool:
    """用已知有效码探活：它也空 = WAF 封禁（而非目标票无数据）。"""
    try:
        return get_tencent_kline(PROBE, days=5, adjust="qfq").empty
    except Exception:
        return True


def append_rows(wc: str, df: pd.DataFrame):
    with LOG.open("a", encoding="utf-8") as f:
        for _, row in df.iterrows():
            rec = row.to_dict()
            rec["code"] = wc
            if "date" in rec and hasattr(rec["date"], "isoformat"):
                rec["date"] = rec["date"].isoformat()[:10]
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=500)
    ap.add_argument("--gap", type=float, default=5.0)
    ap.add_argument("--cooldown-every", type=int, default=50)
    ap.add_argument("--cooldown-sec", type=float, default=30.0)
    ap.add_argument("--waf-backoff", type=float, default=300.0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    done = load_done()
    failed = []
    for line in FAILED.read_text(encoding="utf-8").splitlines():
        try:
            c = json.loads(line)["code"]
            if c not in done:
                failed.append(c)
        except Exception:
            pass
    if args.limit:
        failed = failed[:args.limit]
    print(f"[qfq-retry] todo={len(failed)} gap={args.gap}s cooldown={args.cooldown_sec}s/{args.cooldown_every}", flush=True)

    ok = empty = 0
    for i, wc in enumerate(failed):
        code = wc[2:] if wc.startswith(("sh", "sz", "bj")) else wc
        df = get_tencent_kline(code, days=args.days, adjust="qfq")
        if not df.empty:
            append_rows(wc, df)
            ok += 1
        else:
            # 空结果：探活判断是否 WAF
            if is_waf():
                print(f"  [WAF] at {i+1}/{len(failed)}, backoff {args.waf_backoff}s", flush=True)
                time.sleep(args.waf_backoff)
                # 退避后重试本只
                df = get_tencent_kline(code, days=args.days, adjust="qfq")
                if not df.empty:
                    append_rows(wc, df)
                    ok += 1
                else:
                    empty += 1
            else:
                empty += 1  # 真无数据，不重试
        if (i + 1) % 25 == 0 or i + 1 == len(failed):
            print(f"  {i+1}/{len(failed)} ok={ok} empty={empty}", flush=True)
        if args.cooldown_every and (i + 1) % args.cooldown_every == 0:
            time.sleep(args.cooldown_sec)
        time.sleep(args.gap)


if __name__ == "__main__":
    main()
