"""项目内数据仓同步命令（ADR-0033）。

用法：
    python scripts/data_sync.py --report
    python scripts/data_sync.py --datasets kline,index,snapshot,events --universe top50 --days 250
    python scripts/data_sync.py --datasets financial,fundflow --universe top20

设计：
- 只做「取数 → 写本地仓」，不做分析与选股；
- 每只标的失败不影响其它标的，最后汇总成功/失败；
- 单标的请求间隔 `--sleep`（默认 0.05s），避免把公共网关打到限流。
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _universe(kind: str, limit: int):
    from lib.data.data_layer import get_realtime_quotes
    df = get_realtime_quotes()
    if df is None or df.empty:
        return []
    if "成交额" in df.columns:
        df = df.sort_values("成交额", ascending=False)
    codes = [str(c).zfill(6) for c in df["代码"].astype(str)]
    if kind == "all":
        return codes[:limit] if limit else codes
    if kind == "top50":
        return codes[:50]
    if kind == "top300":
        return codes[:300]
    if kind.startswith("codes="):
        return kind.split("=", 1)[1].split(",")
    return codes[:50]


def sync_kline(codes, days, sleep):
    from lib.data.data_source_registry import get_stock_hist_multi_source
    ok = fail = 0
    for i, code in enumerate(codes, 1):
        try:
            df = get_stock_hist_multi_source(code, days=days, prefer_adjusted=True)
            ok += 0 if df.empty else 1
            fail += 1 if df.empty else 0
        except Exception:
            fail += 1
        if sleep:
            time.sleep(sleep)
        if i % 20 == 0:
            print(f"  kline {i}/{len(codes)} ok={ok} fail={fail}", flush=True)
    return ok, fail


def sync_index(codes, days, sleep):
    from lib.data.data_layer import get_index_hist
    ok = fail = 0
    for code in codes:
        df = get_index_hist(code, days=days)
        ok += 0 if df.empty else 1
        fail += 1 if df.empty else 0
        time.sleep(sleep)
    return ok, fail


def sync_snapshot(sleep):
    from lib.data.data_layer import get_realtime_quotes
    df = get_realtime_quotes(force=True)
    return (1, 0) if df is not None and not df.empty else (0, 1)


def sync_events(sleep):
    from config import get_config
    import lib.strategy.strategies as st
    ok = fail = 0
    for fn in (st.get_zt_candidates, st.get_lhb_candidates):
        df = fn(get_config())
        ok += 0 if df.empty else 1
        fail += 1 if df.empty else 0
    return ok, fail


def sync_financial(codes, sleep):
    from lib.scoring.data_tdx_fin_adapter import get_stock_fundamentals
    ok = fail = 0
    for i, code in enumerate(codes, 1):
        if get_stock_fundamentals(code) is None:
            fail += 1
        else:
            ok += 1
        time.sleep(sleep)
        if i % 20 == 0:
            print(f"  financial {i}/{len(codes)} ok={ok} fail={fail}", flush=True)
    return ok, fail


def sync_fundflow(codes, sleep):
    from lib.data.data_layer import get_stock_fund_flow
    ok = fail = 0
    for code in codes:
        r = get_stock_fund_flow(code)
        if r and (r.get("main_net_ratio") or r.get("net_inflow_5d")):
            ok += 1
        else:
            fail += 1
        time.sleep(sleep)
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="kline,index,snapshot,events",
                    help="逗号分隔：kline,index,snapshot,events,financial,fundflow")
    ap.add_argument("--universe", default="top50", help="top50/top300/all/codes=600519,000001")
    ap.add_argument("--limit", type=int, default=0, help="all 模式下的上限")
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--report", action="store_true", help="只打印本地仓覆盖，不取数")
    args = ap.parse_args()

    from lib.data import local_store

    if args.report:
        cov = local_store.coverage()
        if not cov:
            print("本地仓为空：data/store/")
            return 0
        for ds, info in cov.items():
            print(f"{ds:12s} files={info['files']:5d} rows={info['rows']:8d} updated={info['updated_at']}")
        return 0

    sets = [s.strip() for s in args.datasets.split(",") if s.strip()]
    codes = _universe(args.universe, args.limit)
    print(f"目标数据集: {sets} | universe={args.universe} ({len(codes)} 只) | days={args.days}")

    total_ok = total_fail = 0
    for ds in sets:
        t = time.time()
        if ds == "kline":
            ok, fail = sync_kline(codes, args.days, args.sleep)
        elif ds == "index":
            ok, fail = sync_index(["000300", "000001", "399001"], args.days, args.sleep)
        elif ds == "snapshot":
            ok, fail = sync_snapshot(args.sleep)
        elif ds == "events":
            ok, fail = sync_events(args.sleep)
        elif ds == "financial":
            ok, fail = sync_financial(codes, args.sleep)
        elif ds == "fundflow":
            ok, fail = sync_fundflow(codes, args.sleep)
        else:
            print(f"未知数据集: {ds}")
            continue
        total_ok += ok; total_fail += fail
        print(f"[{ds}] ok={ok} fail={fail} {time.time()-t:.1f}s")

    print("\n本地仓覆盖：")
    for ds, info in local_store.coverage().items():
        print(f"  {ds:12s} files={info['files']:5d} rows={info['rows']:8d}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
