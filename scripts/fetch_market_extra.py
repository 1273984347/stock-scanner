"""补充数据拉取：市场级/榜单级 + 逐只级（新闻/研报/公告/资金流/北向/南下）。

数据源：westock Go CLI（已鉴权）。断点续传：逐批/逐只 jsonl 落盘。

用法：
    python scripts/fetch_market_extra.py --mode market    # 市场级+榜单级(快)
    python scripts/fetch_market_extra.py --mode stock --kinds news,fund
    python scripts/fetch_market_extra.py --resume
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
from lib.data.data_westock import _parse_markdown_table  # noqa: E402
from fetch_market_bulk import GO_CLI, OUT, run_cli  # noqa: E402

EXTRA = OUT / "_extra"
LOG = EXTRA / "_logs"


def save(name: str, df: pd.DataFrame):
    if df.empty:
        return False
    df.to_parquet(EXTRA / f"{name}.parquet", index=False)
    print(f"  saved {name}: {df.shape}", flush=True)
    return True


def fetch_market_level():
    """市场级 + 榜单级：板块榜/估值、指数清单、宏观、ETF榜、热搜榜。"""
    EXTRA.mkdir(parents=True, exist_ok=True)
    tasks = [
        ("sector_ranking_industry", ["sector", "ranking", "--kind", "industry", "--limit", "200"]),
        ("sector_ranking_concept", ["sector", "ranking", "--kind", "concept", "--limit", "200"]),
        ("index_list_hs", ["index", "list", "--limit", "2000"]),
        ("index_list_hk", ["index", "list", "--market", "hk", "--limit", "200"]),
        ("index_list_us", ["index", "list", "--market", "us", "--limit", "200"]),
        ("macro_cn_core", ["macro", "indicator", "cn_core", "--limit", "60"]),
        ("hot_stock", ["hot", "stock", "--limit", "100"]),
        ("hot_news", ["hot", "news", "--limit", "100"]),
        ("hot_etf", ["hot", "etf", "--limit", "100"]),
        ("hot_sector", ["hot", "sector", "--limit", "100"]),
    ]
    for name, args in tasks:
        print(f"[market] {name} ...", flush=True)
        out, rc = run_cli(args)
        save(name, _parse_markdown_table(out))
        time.sleep(1)
    # 沪深300/中证500/上证50 成份
    for idx, code in [("hs300", "sh000300"), ("zz500", "sh000905"), ("sz50", "sh000016")]:
        print(f"[index constituent] {idx} ...", flush=True)
        out, rc = run_cli(["index", "constituent", code])
        save(f"index_constituent_{idx}", _parse_markdown_table(out))
        time.sleep(1)


def stock_universes():
    us = {}
    for m in ("hs", "hk", "us"):
        p = UNIVERSE / f"universe_{m}.txt"
        if p.exists():
            us[m] = [c.strip() for c in p.read_text(encoding="utf-8").splitlines() if c.strip()]
    return us


BATCH_SIZE = {"news": 10, "report": 10, "notice": 10, "fund": 20,
              "north": 10, "south": 10}


def fetch_batch(kind: str, codes: list):
    joined = ",".join(codes)
    if kind == "news":
        return ["news", "list", joined, "--limit", "20"]
    if kind == "report":
        return ["report", "list", joined, "--limit", "20"]
    if kind == "notice":
        return ["notice", "list", joined, "--limit", "20"]
    if kind == "fund":
        return ["fund", "flow", joined]
    if kind == "north":
        return ["fund", "north-holding", joined]
    if kind == "south":
        return ["fund", "south-holding", joined]
    raise ValueError(kind)


def parse_grouped(out: str, codes: list, kind: str) -> list:
    """解析分组输出：news/report/notice 按 **code** 分块；fund/north 单表带 code 列；
    south 单表或 **code name** 分组。"""
    records = []
    if kind in ("fund", "north"):
        df = _parse_markdown_table(out)
        for _, row in df.iterrows():
            rec = row.to_dict()
            records.append(rec)
        return records
    if kind == "south":
        df = _parse_markdown_table(out)
        if not df.empty and "code" in df.columns:
            return [row.to_dict() for _, row in df.iterrows()]
        # 南下批量按 **code name** 分组返回
        import re
        parts = re.split(r"^\*\*(hk[0-9]+)[^*]*\*\*", out, flags=re.MULTILINE)
        for i in range(1, len(parts) - 1, 2):
            for _, row in _parse_markdown_table(parts[i + 1]).iterrows():
                rec = row.to_dict()
                rec["code"] = parts[i]
                records.append(rec)
        return records
    # **code** 分组：逐块解析该 code 的表格
    import re
    parts = re.split(r"^\*\*([a-z]{2}[A-Za-z0-9.]+)\*\*\s*$", out, flags=re.MULTILINE)
    # parts = [pre, code1, block1, code2, block2, ...]
    for i in range(1, len(parts) - 1, 2):
        code, block = parts[i], parts[i + 1]
        df = _parse_markdown_table(block)
        for _, row in df.iterrows():
            rec = row.to_dict()
            rec["code"] = code
            records.append(rec)
    return records


def done_codes(kind: str) -> set:
    log = LOG / f"{kind}.jsonl"
    if not log.exists():
        return set()
    done = set()
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            if r.get("_ok"):
                done.add(r["_code"])
        except Exception:
            pass
    return done


def fetch_stock_level(kinds: list, markets: list, sleep: float):
    LOG.mkdir(parents=True, exist_ok=True)
    universes = stock_universes()
    for kind in kinds:
        # north/south 只对 A股/港股有意义；fund 全市场
        targets = []
        for m in markets:
            if m not in universes:
                continue
            if kind == "north" and m != "hs":
                continue
            if kind == "south" and m != "hk":
                continue
            if kind == "fund" and m == "us":  # 美股无 fund flow（skill 能力边界）
                continue
            targets += [(m, c) for c in universes[m]]
        done = done_codes(kind)
        todo = [(m, c) for m, c in targets if c not in done]
        bs = BATCH_SIZE[kind]
        print(f"[stock/{kind}] total={len(targets)} done={len(done)} todo={len(todo)} batch={bs}", flush=True)
        log = LOG / f"{kind}.jsonl"
        ok = fail = 0
        # 按批次分组（保留市场归属）
        batches = [todo[i:i+bs] for i in range(0, len(todo), bs)]
        for bi, batch in enumerate(batches):
            codes = [c for _, c in batch]
            cmap = dict(batch)
            out, rc = run_cli(fetch_batch(kind, codes), timeout=90, retries=2)
            records = parse_grouped(out, codes, kind)
            got_codes = {r.get("code") or r.get("SecuCode") or r.get("symbol") for r in records}
            with log.open("a", encoding="utf-8") as f:
                for rec in records:
                    c = rec.get("code") or rec.get("SecuCode") or rec.get("symbol")
                    rec["_code"] = c
                    rec["_market"] = cmap.get(c, c[:2] if c else "")
                    rec["_ok"] = True
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                # 未返回的 code 记 fail，便于续跑补
                for c in codes:
                    if c not in got_codes:
                        f.write(json.dumps({"_code": c, "_market": cmap.get(c), "_ok": False},
                                           ensure_ascii=False) + "\n")
                        fail += 1
            ok += len(got_codes)
            if (bi + 1) % 20 == 0 or bi + 1 == len(batches):
                print(f"  batch {bi+1}/{len(batches)} ok={ok} fail={fail}", flush=True)
            time.sleep(sleep)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["market", "stock"], default="market")
    ap.add_argument("--kinds", default="news,report,notice,fund,north,south")
    ap.add_argument("--markets", default="hs,hk,us")
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()
    if args.mode == "market":
        fetch_market_level()
    else:
        fetch_stock_level([k.strip() for k in args.kinds.split(",")],
                          [m.strip() for m in args.markets.split(",")], args.sleep)
