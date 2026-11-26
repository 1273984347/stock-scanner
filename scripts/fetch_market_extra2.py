"""补充第二批数据：市场级（龙虎榜/涨跌分布/大盘画像/日历/IPO/指数表现）+ 板块深度。

用法：
    python scripts/fetch_market_extra2.py --mode market
    python scripts/fetch_market_extra2.py --mode sector
"""
import argparse
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))
from lib.data.data_westock import _parse_markdown_table  # noqa: E402
from fetch_market_bulk import OUT, run_cli  # noqa: E402

EXTRA = OUT / "_extra2"
EXTRA.mkdir(parents=True, exist_ok=True)


def save(name, out):
    df = _parse_markdown_table(out)
    if not df.empty:
        df.to_parquet(EXTRA / f"{name}.parquet", index=False)
        print(f"  {name}: {df.shape}", flush=True)
    else:
        print(f"  {name}: EMPTY", flush=True)


def market_level():
    tasks = [
        ("lhb_institution", ["lhb", "--type", "institution"]),
        ("lhb_activeseat", ["lhb", "--type", "activeseat"]),
        ("lhb_winbuy", ["lhb", "--type", "winbuy"]),
        ("lhb_winseat", ["lhb", "--type", "winseat"]),
        ("changedist", ["changedist"]),
        ("market_overview_all", ["market-overview", "--type", "all"]),
        ("market_overview_list", ["market-overview", "list"]),
        ("trade_calendar_hs_2026", ["trade-calendar", "--market", "hs", "--year", "2026", "--trading-only", "--limit", "400"]),
        ("trade_calendar_hk_2026", ["trade-calendar", "--market", "hk", "--year", "2026", "--trading-only", "--limit", "400"]),
        ("trade_calendar_us_2026", ["trade-calendar", "--market", "us", "--year", "2026", "--trading-only", "--limit", "400"]),
        ("ipo_hs", ["ipo", "--market", "hs"]),
        ("ipo_hk", ["ipo", "--market", "hk"]),
        ("ipo_us", ["ipo", "--market", "us"]),
        ("index_performance_hk", ["index", "performance"]),
    ]
    for name, args in tasks:
        print(f"[market] {name} ...", flush=True)
        out, rc = run_cli(args, timeout=60, retries=2)
        save(name, out)
        time.sleep(1)


def sector_depth():
    """板块深度数据：需先拿板块代码，再逐板块拉（限量避免过长）。"""
    # 用已有的行业板块榜代码
    rank = pd.read_parquet(OUT / "_extra" / "sector_ranking_industry.parquet")
    codes = rank["code"].dropna().tolist()[:50]  # 前50个行业
    print(f"[sector] {len(codes)} industries", flush=True)
    tasks = []
    for code in codes:
        for kind, name in [("constituent", "constituent"), ("valuation", "valuation"),
                           ("finance", "finance"), ("oper", "oper"), ("forecast", "forecast")]:
            tasks.append((f"sector_{name}_{code}", ["sector", kind, code]))
    for i, (name, args) in enumerate(tasks):
        out, rc = run_cli(args, timeout=60, retries=2)
        save(name, out)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(tasks)}", flush=True)
        time.sleep(0.8)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["market", "sector"], default="market")
    args = ap.parse_args()
    if args.mode == "market":
        market_level()
    else:
        sector_depth()
