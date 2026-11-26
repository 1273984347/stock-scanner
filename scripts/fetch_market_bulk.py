"""全市场四类数据全量拉取（行情/K线/财务/技术指标），带断点续传。

数据源：westock Go CLI（腾讯自选股，专属 APIKey 已鉴权）。
范围：沪深京 A股 + 港股 + 美股普通股，共 ~16152 只。

用法：
    python scripts/fetch_market_bulk.py --categories quote,kline,finance,technical
    python scripts/fetch_market_bulk.py --markets hs --categories kline   # 只拉 A股 K线
    python scripts/fetch_market_bulk.py --resume                          # 断点续传（默认行为）

设计（断点续传）：
- 每个 (market, category, chunk) 一个 parquet 文件 + 同名 .done 标记
- 重启自动跳过已有 .done 的分片；失败分片留 .err，可单独重跑
- 批量上限：quote 50 / kline 20 / finance 10 / technical 20（实测）
- 失败不中断整批，记录到 errors.jsonl 后继续
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from bulk_paths import RAW, UNIVERSE, REPORTS  # noqa: E402
from lib.data.data_westock import _parse_markdown_table  # noqa: E402  复用判据保护的解析器

GO_CLI = os.environ.get("WESTOCK_GO_CLI", r"C:\Users\12739\.local\bin\westock.exe")
OUT = ROOT / "data" / "exports" / "bulk"
DONE_DIR = OUT / "_done"
ERR_LOG = OUT / "errors.jsonl"

# 批量上限（实测）
BATCH_SIZE = {"quote": 50, "kline": 20, "finance": 10, "technical": 20}
# 类别 → CLI 命令构造
def build_cmd(category: str, codes: List[str]) -> List[str]:
    joined = ",".join(codes)
    if category == "quote":
        return ["quote", joined]
    if category == "kline":
        return ["kline", joined, "--period", "day", "--limit", "2000", "--fq", "qfq"]
    if category == "finance":
        return ["finance", joined, "--type", "income", "--fields", "all", "--limit", "40"]
    if category == "technical":
        return ["technical", joined, "--period", "day", "--limit", "500"]
    raise ValueError(f"unknown category: {category}")


RATE_LIMIT_MARK = "服务限频"


def run_cli(args: List[str], timeout: int = 180, retries: int = 4) -> Tuple[str, int]:
    """执行 CLI；遇服务限频按 30/60/120/240s 指数退避重试（限频窗口实测 ~30-40s）。"""
    for attempt in range(retries):
        try:
            r = subprocess.run([GO_CLI] + args, capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8")
            if RATE_LIMIT_MARK in (r.stdout or ""):
                if attempt < retries - 1:
                    backoff = 30 * (2 ** attempt)
                    print(f"  [rate-limited] backoff {backoff}s ...", flush=True)
                    time.sleep(backoff)
                    continue
                return r.stdout, -2  # 限频耗尽
            return r.stdout, r.returncode
        except subprocess.TimeoutExpired:
            if attempt < retries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            return "", -1
    return "", -3


def fetch_universe(market: str, force: bool = False) -> List[str]:
    """从 CLI 拉证券池代码（普通股/stock），落盘缓存避免重跑反复调 API。"""
    cache = UNIVERSE / f"universe_{market}.txt"
    if cache.exists() and not force:
        return [c.strip() for c in cache.read_text(encoding="utf-8").splitlines() if c.strip()]
    mtype = "stock"
    out, rc = run_cli(["universe", "--market", market, "--type", mtype, "--limit", "12000"])
    if rc != 0:
        raise RuntimeError(f"universe {market} failed rc={rc}: {out[:200]}")
    df = _parse_markdown_table(out)
    if df.empty or "code" not in df.columns:
        raise RuntimeError(f"universe {market} returned no codes: {out[:200]}")
    codes = [c for c in df["code"].tolist() if c and str(c).startswith(("sh", "sz", "bj", "hk", "us"))]
    OUT.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(codes), encoding="utf-8")
    return codes


def process_chunk(market: str, category: str, chunk_idx: int, codes: List[str],
                  force: bool = False) -> bool:
    done = DONE_DIR / f"{market}_{category}_{chunk_idx:04d}.done"
    target = OUT / f"{market}_{category}_{chunk_idx:04d}.parquet"
    if done.exists() and target.exists() and not force:
        return True  # 断点续传：已完成
    out, rc = run_cli(build_cmd(category, codes))
    if rc != 0 or not out.strip():
        ERR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ERR_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"market": market, "category": category,
                                "chunk": chunk_idx, "rc": rc,
                                "preview": out[:300]}, ensure_ascii=False) + "\n")
        return False
    df = _parse_markdown_table(out)
    if df.empty:
        # 空结果也算成功（部分次新股无财务数据），落空表标记防重复拉
        df = pd.DataFrame()
    df.to_parquet(target, index=False)
    done.touch()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="hs,hk,us", help="逗号分隔 hs/hk/us")
    ap.add_argument("--categories", default="quote,kline,finance,technical")
    ap.add_argument("--force", action="store_true", help="忽略 .done 强制重拉")
    ap.add_argument("--sleep", type=float, default=5.0, help="每批间隔秒（限流保护，实测~30s窗口）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    markets = [m.strip() for m in args.markets.split(",")]
    categories = [c.strip() for c in args.categories.split(",")]

    for market in markets:
        codes = fetch_universe(market)
        print(f"[{market}] universe: {len(codes)} codes", flush=True)
        for category in categories:
            bs = BATCH_SIZE[category]
            chunks = [codes[i:i+bs] for i in range(0, len(codes), bs)]
            todo = [i for i in range(len(chunks))
                    if args.force or not (DONE_DIR / f"{market}_{category}_{i:04d}.done").exists()]
            print(f"[{market}/{category}] {len(chunks)} chunks, {len(todo)} todo", flush=True)
            ok = fail = 0
            t0 = time.time()
            for n, idx in enumerate(todo):
                success = process_chunk(market, category, idx, chunks[idx], force=args.force)
                ok += success
                fail += (not success)
                if (n + 1) % 20 == 0 or n + 1 == len(todo):
                    elapsed = time.time() - t0
                    print(f"  {n+1}/{len(todo)} ok={ok} fail={fail} "
                          f"elapsed={elapsed:.0f}s eta={elapsed/(n+1)*(len(todo)-n-1):.0f}s", flush=True)
                time.sleep(args.sleep)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
