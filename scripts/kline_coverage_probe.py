"""日线覆盖探测（ADR-0027，2026-11-19）。

用途：一条命令回答「个股/指数日线现在还通不通、数据到不到最近交易日」。
6 个代表标的覆盖沪 / 深 / 创业板 / 科创板 / 北交所 / 指数——任一为 0 或日期陈旧
就说明源栈在该市场段失效。

用法：
    python scripts/kline_coverage_probe.py            # 报告，exit 0
    python scripts/kline_coverage_probe.py --strict   # 任一失败 exit 1（运维/CI 用）

注意：期望日期按「工作日 + 15:30 后算当日」推算，**不识别法定节假日**；
节假日期间请用 --expect-date 显式指定。
"""
import argparse
import pathlib
import sys
from datetime import date, datetime, timedelta

# 脚本从 scripts/ 启动时 sys.path[0] 是 scripts/，需显式把仓库根加进来
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# 代表标的：(代码, 市场段, 是否指数)。
# 必须显式标记——000001 既是平安银行（深主板）也是上证指数，按代码猜必错。
CASES = [
    ("600519", "沪主板", False),
    ("000001", "深主板", False),
    ("300750", "创业板", False),
    ("688981", "科创板", False),
    ("920000", "北交所", False),
    ("000300", "指数(沪深300)", True),
]


def last_trading_day(now: datetime = None) -> date:
    """最近一个可能有收盘数据的交易日（工作日 + 15:30 规则；不含节假日表）。"""
    now = now or datetime.now()
    d = now.date()
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def probe(code: str, is_index: bool = False) -> tuple:
    """返回 (source, rows, last_date, error)。"""
    from lib.data.data_source_registry import (BJ_SOURCE_PRIORITY,
                                              DATA_SOURCE_PRIORITY_ADJUSTED,
                                              _fetch_from_source, _is_bj_stock)
    from lib.data.data_kline_web import get_index_kline_web

    if is_index:
        df = get_index_kline_web(code, days=60)
        src = "index_web"
    else:
        priority = BJ_SOURCE_PRIORITY if _is_bj_stock(code) else DATA_SOURCE_PRIORITY_ADJUSTED
        src, df = "none", None
        for s in priority:
            candidate = _fetch_from_source(code, 60, s)
            if candidate is not None and len(candidate) >= 10:
                src, df = s, candidate
                break
    if df is None or len(df) < 10:
        return src, 0, "", "无有效数据（<10 根）"
    last = str(df["date"].max().date()) if "date" in df.columns else ""
    return src, len(df), last, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="任一失败退出码 1")
    ap.add_argument("--expect-date", default="", help="期望的最新日期 YYYY-MM-DD")
    ap.add_argument("--min-rows", type=int, default=40)
    args = ap.parse_args()

    expect = args.expect_date or last_trading_day().isoformat()
    print(f"期望最新日期：{expect}  （标的数 {len(CASES)}）")
    print(f"{'代码':<8}{'市场段':<14}{'源':<16}{'行数':>5}  {'最新':<12}结论")
    print("-" * 78)

    failed = 0
    for code, label, is_index in CASES:
        src, rows, last, err = probe(code, is_index)
        ok = (not err) and rows >= args.min_rows and last == expect
        if not ok:
            failed += 1
        reason = err or ("" if last == expect else f"最新 {last} ≠ 期望 {expect}")
        print(f"{code:<8}{label:<14}{src:<16}{rows:>5}  {last:<12}{'OK' if ok else 'FAIL ' + reason}")

    print("-" * 78)
    print(f"通过 {len(CASES) - failed}/{len(CASES)}；源栈："
          f"默认 sina_kline → tencent_kline，回测 tencent_kline(qfq) 领头")
    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
