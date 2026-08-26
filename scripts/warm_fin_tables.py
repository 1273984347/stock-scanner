"""预热板块成分股财报表磁盘缓存（并行版）

背景：akshare 东财财报接口按报告期逐页 HTTP 拉取，单股串行约 120s，
A/B 回测 ON 分支冷启动需数百只股票，串行不可行。
本脚本在回测前一次性并行预填 cache/industry/tables/{code}.pkl，
回测时全部命中磁盘缓存（TTL 与板块缓存一致，7 天内有效复用）。

用法：
    $env:PYTHONPATH="<项目根>"
    python scripts/warm_fin_tables.py --dry-run     # 仅枚举规模
    python scripts/warm_fin_tables.py --workers 8   # 并行预热
    python scripts/warm_fin_tables.py --workers 6 --delay 1.5   # 温和模式（防新浪限流）

注意：新浪接口对突发并发敏感，workers 过大时会静默降级到东财逐期（慢 ~13 倍）。
日志出现"财报数据改用东财逐期接口"即为限流信号，应降低 workers / 加大 delay 重跑。
"""
import argparse
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.data_industry import _TABLES_CACHE_DIR, _fetch_raw_tables
from lib.data_layer import get_block_stocks, get_stock_blocks

# 与 scripts/backtest_top_warning_ab.py 保持一致的标的池
SYMBOLS = ["600519", "000858", "601318", "600036", "000333",
           "601012", "600887", "000651", "600276", "300750"]


def collect_targets():
    """10 标的 → 所属板块并集 → 成分股并集

    成分股按 sorted()[:max_stocks_per_block] 截断，
    与 data_industry._block_codes + fetch 截断行为一致。
    """
    from config import CycleWarningConfig

    blocks, seen_blocks = [], set()
    for sym in SYMBOLS:
        for b in get_stock_blocks(sym):
            if b not in seen_blocks:
                seen_blocks.add(b)
                blocks.append(b)

    max_stocks = CycleWarningConfig().max_stocks_per_block
    codes, seen_codes = [], set()
    for b in blocks:
        # 仅取运行时实际会请求的前 max_stocks 只（sorted 后截断）
        for c in sorted(get_block_stocks(b))[:max_stocks]:
            if c not in seen_codes:
                seen_codes.add(c)
                codes.append(c)
    return blocks, codes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8, help="并发线程数")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="每任务前随机延迟上限秒数（防数据源限流），0 关闭")
    ap.add_argument("--dry-run", action="store_true", help="仅枚举规模不抓取")
    args = ap.parse_args()
    # INFO 级可见"改用东财逐期"降级日志，作为限流金丝
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    blocks, codes = collect_targets()
    todo = [c for c in codes if not (_TABLES_CACHE_DIR / f"{c}.pkl").exists()]
    print(f"板块 {len(blocks)} 个 / 股票 {len(codes)} 只 / 待预热 {len(todo)} 只 "
          f"(workers={args.workers})", flush=True)

    if args.dry_run:
        return

    stats = {"ok": 0, "fail": 0}
    failures = []

    def worker(code):
        if args.delay > 0:
            time.sleep(random.uniform(0.2, args.delay))
        t0 = time.time()
        try:
            _fetch_raw_tables(code)
            return code, True, f"{time.time() - t0:.1f}s"
        except Exception as e:
            return code, False, str(e)

    t_start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(worker, c) for c in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            code, ok, info = fut.result()
            if ok:
                stats["ok"] += 1
            else:
                stats["fail"] += 1
                failures.append((code, info))
            if i % 10 == 0 or i == len(futs):
                elapsed = time.time() - t_start
                eta = elapsed / i * (len(futs) - i)
                print(f"[{i}/{len(futs)}] ok={stats['ok']} fail={stats['fail']} "
                      f"elapsed={elapsed:.0f}s eta={eta:.0f}s", flush=True)

    print(f"预热完成：ok={stats['ok']} fail={stats['fail']} "
          f"总耗时 {time.time() - t_start:.0f}s", flush=True)
    if failures:
        print("失败清单（前 30 条）：")
        for code, err in failures[:30]:
            print(f"  {code}: {err}")


if __name__ == "__main__":
    main()
