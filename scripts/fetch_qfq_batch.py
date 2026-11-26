"""前复权分批拉取（守精度红线，避开腾讯 WAF 累计封禁）。

策略：
- 每次运行只拉一批（默认 100 只），拉完自动退出 —— 避免累计请求触发 WAF
- 启动前先探测 WAF；封禁则每 60s 探一次，最多等 --wait-min 分钟
- 解封后逐只拉（间隔 2s），批次内若再遇 WAF 立即停止（保留断点，下次续）
- 断点续传：跳过 hs_qfq.jsonl 中已完成的 code

用法：
    python scripts/fetch_qfq_batch.py --size 100 --wait-min 120
"""
import argparse
import json
import subprocess
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

DIR = OUT / "_qfq"
LOG = DIR / "hs_qfq.jsonl"
PROBE = "600519"


def waf_blocked() -> bool:
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", "10",
             "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,3,qfq"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        return out.startswith("<!DOCTYPE") or '"code":0' not in out
    except Exception:
        return True


def load_done() -> set:
    if not LOG.exists():
        return set()
    done = set()
    for line in LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            done.add(json.loads(line)["code"])
        except Exception:
            pass
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=100)
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--wait-min", type=int, default=120)
    ap.add_argument("--days", type=int, default=500)
    args = ap.parse_args()

    # 1. 等 WAF 解封
    waited = 0
    while waf_blocked():
        if waited >= args.wait_min * 60:
            print(f"[WAF] 等待 {args.wait_min} 分钟仍封禁，退出（不改数据）", flush=True)
            return
        if waited % 300 == 0:
            print(f"[WAF] 封禁中，已等 {waited//60} 分钟...", flush=True)
        time.sleep(60)
        waited += 60
    print(f"[WAF] 已解封（等待 {waited//60} 分钟）", flush=True)

    # 2. 取一批待办
    done = load_done()
    universe = [c.strip() for c in (UNIVERSE / "universe_hs.txt").read_text(encoding="utf-8").splitlines() if c.strip()]
    todo = [c for c in universe if c not in done][:args.size]
    print(f"[batch] todo={len(todo)} (remaining total={len(universe)-len(done)})", flush=True)

    ok = 0
    for i, wc in enumerate(todo):
        code = wc[2:] if wc.startswith(("sh", "sz", "bj")) else wc
        df = get_tencent_kline(code, days=args.days, adjust="qfq")
        if df.empty:
            if waf_blocked():
                print(f"[WAF] 批次内第 {i+1} 只触发封禁，停止（已完成 {ok} 只）", flush=True)
                break
            continue
        with LOG.open("a", encoding="utf-8") as f:
            for _, row in df.iterrows():
                rec = row.to_dict()
                rec["code"] = wc
                if "date" in rec and hasattr(rec["date"], "isoformat"):
                    rec["date"] = rec["date"].isoformat()[:10]
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        ok += 1
        time.sleep(args.gap)
    print(f"[batch done] 本批完成 {ok}/{len(todo)} 只", flush=True)


if __name__ == "__main__":
    main()
