"""全量数据抽样质量检查：前复权口径 / 日期对齐 / OHLC逻辑 / 成交量单位 / 数值可解析。

产物在 data/exports/bulk/*.parquet（字符串类型，Markdown 解析原样保留）。
本脚本只读盘 + 少量 CLI 对照调用，不改数据。

判据：
1. 数值可解析：open/last/high/low/volume/amount 必须能转 float，异常率 = 0
2. OHLC 逻辑：high >= max(open,last) 且 low <= min(open,last)，违反率 < 0.1%
3. 日期对齐：无未来日期（> 运行日）；K线最新日 == 技术最新日（抽样）；日期升序无重复
4. 成交量单位：A股 volume 为手（对比 amount/price 推断），港美股为股
5. 前复权口径：CLI --fq qfq 与默认(不复权)/hfq 的对照——检测 qfq 参数是否真的生效

用法：
    python scripts/audit_bulk_quality.py
    python scripts/audit_bulk_quality.py --sample 20   # 每市场抽样标的数
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.data.data_westock import _parse_markdown_table, _to_westock_code  # noqa: E402
from fetch_market_bulk import GO_CLI  # noqa: E402

OUT = ROOT / "data" / "exports" / "bulk"
TODAY = pd.Timestamp.now().date()


def num(s):
    return pd.to_numeric(s, errors="coerce")


def load_kline(market: str) -> pd.DataFrame:
    parts = [pd.read_parquet(f) for f in OUT.glob(f"{market}_kline_*.parquet")]
    parts += [pd.read_parquet(f) for f in OUT.glob(f"{market}_kline_*repair*.parquet")]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def check_numeric_ohlc(market: str):
    df = load_kline(market)
    if df.empty:
        return {"market": market, "error": "no data"}
    n = len(df)
    o, c, h, l = (num(df[k]) for k in ("open", "last", "high", "low"))
    vol, amt = num(df["volume"]), num(df["amount"])
    parse_fail = int(o.isna().sum() + c.isna().sum() + h.isna().sum() + l.isna().sum())
    # 数据源早期历史有 OHLC=0 占位行（非逻辑违反），先剔除再判 OHLC 一致性
    zero_ohlc = (o == 0) | (h == 0) | (l == 0)
    bad_all = (h < o) | (h < c) | (l > o) | (l > c)
    bad_nonzero = int((bad_all & ~zero_ohlc).sum())
    zero_vol = int((vol <= 0).sum())
    zero_price = int((c <= 0).sum())
    return {
        "market": market, "rows": n,
        "parse_fail_cells": parse_fail, "parse_fail_pct": round(parse_fail/(n*4)*100, 6),
        "ohlc_zero_rows": int(zero_ohlc.sum()), "ohlc_zero_pct": round(zero_ohlc.mean()*100, 4),
        "bad_ohlc_nonzero": bad_nonzero, "bad_ohlc_nonzero_pct": round(bad_nonzero/n*100, 6),
        "zero_volume": zero_vol, "zero_price": zero_price,
    }


def check_dates(market: str):
    df = load_kline(market)
    if df.empty:
        return {"market": market, "error": "no data"}
    d = pd.to_datetime(df["date"], errors="coerce")
    future = int((d.dt.date > TODAY).sum())
    null_dates = int(d.isna().sum())
    latest = str(d.max().date()) if not d.empty else None
    earliest = str(d.min().date()) if not d.empty else None
    # CLI 按新→旧倒序返回（设计如此）：检查每只票日期是否"单调"(升或降均可)，重复才是问题
    sample_codes = df["code"].dropna().unique()[:20]
    non_monotonic = 0
    dup = 0
    for code in sample_codes:
        sub = d[df["code"] == code].sort_index().dropna()
        if len(sub) > 1:
            if not (sub.is_monotonic_increasing or sub.is_monotonic_decreasing):
                non_monotonic += 1
        if sub.duplicated().any():
            dup += 1
    return {
        "market": market, "earliest": earliest, "latest": latest,
        "future_dates": future, "null_dates": null_dates,
        "sample_non_monotonic_codes": non_monotonic, "sample_dup_date_codes": dup,
    }


def check_volume_unit(market: str):
    """用 amount/close/volume 比值推断单位：A股≈100(手)，港美股≈1(股)。"""
    df = load_kline(market)
    if df.empty:
        return {"market": market, "error": "no data"}
    c, v, a = num(df["last"]), num(df["volume"]), num(df["amount"])
    valid = (c > 0) & (v > 0) & (a > 0)
    ratio = (a / (c * v))[valid].replace([float("inf")], float("nan")).dropna()
    if ratio.empty:
        return {"market": market, "error": "no valid ratio"}
    return {
        "market": market,
        "amount/(close*volume) median": round(float(ratio.median()), 4),
        "p10": round(float(ratio.quantile(0.1)), 4),
        "p90": round(float(ratio.quantile(0.9)), 4),
        "inferred_unit": "手(lots)" if ratio.median() > 50 else "股(shares)",
    }


def check_adjust_contract(sample_codes):
    """CLI 对照：qfq vs 默认(不复权) vs hfq——检测 --fq qfq 是否真生效。"""
    results = []
    for code in sample_codes:
        wc = _to_westock_code(code)
        closes = {}
        for tag, extra in [("qfq", ["--fq", "qfq"]), ("raw", []), ("hfq", ["--fq", "hfq"])]:
            cmd = [GO_CLI, "kline", wc, "--period", "day", "--start", "2024-06-17",
                   "--end", "2024-06-21"] + extra
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8")
                df = _parse_markdown_table(r.stdout)
                closes[tag] = df["last"].tolist() if "last" in df.columns else []
            except Exception:
                closes[tag] = []
        if closes["qfq"] and closes["raw"]:
            same_qfq_raw = closes["qfq"] == closes["raw"]
        else:
            same_qfq_raw = None
        if closes["qfq"] and closes["hfq"]:
            same_qfq_hfq = closes["qfq"] == closes["hfq"]
        else:
            same_qfq_hfq = None
        results.append({
            "code": wc, "qfq": closes["qfq"][:2], "raw": closes["raw"][:2],
            "hfq": closes["hfq"][:2],
            "qfq_equals_raw": same_qfq_raw, "qfq_equals_hfq": same_qfq_hfq,
        })
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=3)
    args = ap.parse_args()
    print(f"运行日: {TODAY}\n")
    print("=== 1. 数值可解析 + OHLC 逻辑 ===")
    numeric = [check_numeric_ohlc(m) for m in ("hs", "hk", "us")]
    print(pd.DataFrame(numeric).to_string(index=False))
    print("\n=== 2. 日期对齐 ===")
    dates = [check_dates(m) for m in ("hs", "hk", "us")]
    print(pd.DataFrame(dates).to_string(index=False))
    print("\n=== 3. 成交量单位 ===")
    vol = [check_volume_unit(m) for m in ("hs", "hk", "us")]
    print(pd.DataFrame(vol).to_string(index=False))
    print("\n=== 4. 前复权口径对照（2024-06-17~21 窗口）===")
    # 选有除权史的标的
    codes = ["600519", "000002", "000651", "600036"][:args.sample]
    adj = check_adjust_contract(codes)
    for r in adj:
        print(json.dumps(r, ensure_ascii=False))
    # 汇总判据
    print("\n=== 判据汇总 ===")
    all_parse = all(r.get("parse_fail_pct", 0) == 0 for r in numeric)
    all_ohlc = all(r.get("bad_ohlc_nonzero_pct", 100) < 0.1 for r in numeric)
    all_future = all(r.get("future_dates", 1) == 0 for r in dates)
    no_dup = all(r.get("sample_dup_date_codes", 1) == 0 for r in dates)
    no_nonmono = all(r.get("sample_non_monotonic_codes", 1) == 0 for r in dates)
    qfq_noop = all(r.get("qfq_equals_raw") is True for r in adj if r.get("qfq_equals_raw") is not None)
    print(f"[{'PASS' if all_parse else 'FAIL'}] 数值可解析（0 解析失败）")
    print(f"[{'PASS' if all_ohlc else 'FAIL'}] OHLC 逻辑（剔除数据源 OHLC=0 后违反率<0.1%）")
    print(f"[{'PASS' if all_future else 'FAIL'}] 无未来日期")
    print(f"[{'PASS' if no_dup and no_nonmono else 'FAIL'}] 日期单调无重复（CLI 倒序为设计）")
    print(f"[{'FAIL' if qfq_noop else 'PASS'}] qfq 参数生效性: "
          f"{'qfq == 不复权（参数不生效！K线实为不复权价）' if qfq_noop else 'qfq ≠ 不复权（参数生效）'}")


if __name__ == "__main__":
    main()
