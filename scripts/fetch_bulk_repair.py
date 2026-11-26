"""补拉 CLI 批量局部降级造成的逐码缺口（稳健版：逐只落盘 + 断点续传）。

根因（P-129）：批量混入弱源代码时该码返空，外层按 batch 标 done，缺逐码校验。
本版改进（相对旧版）：
- 每补一只立即追加到 jsonl 日志（进程中断不丢已完成部分）
- 单只 CLI 超时 60s（旧版 180s 会把限频退避拖成数小时空转）
- 限频不反复退避：只等一次 40s，仍失败记入 failed 跳过，下轮续跑再试
- 支持 --resume 从 jsonl 恢复已补代码

用法：
    python scripts/fetch_bulk_repair.py --market hs --category kline
    python scripts/fetch_bulk_repair.py --all --dry-run
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
from fetch_market_bulk import GO_CLI, OUT, build_cmd  # noqa: E402

LOG_DIR = OUT / "_repair_logs"


def existing_codes(market: str, category: str) -> set:
    got = set()
    for f in OUT.glob(f"{market}_{category}_*.parquet"):
        df = pd.read_parquet(f)
        if "code" in df.columns:
            got.update(df["code"].dropna().tolist())
    return got


def already_repaired(market: str, category: str) -> set:
    log = LOG_DIR / f"{market}_{category}.jsonl"
    if not log.exists():
        return set()
    done = set()
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("ok"):
                done.add(rec["code"])
        except Exception:
            continue
    return done


def fetch_one(code: str, category: str, timeout: int = 60):
    """单只拉取；限频只等一次 40s，仍失败返回 (空, 原因)。"""
    for attempt in range(2):
        try:
            r = subprocess.run([GO_CLI] + build_cmd(category, [code]),
                               capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8")
            out = r.stdout or ""
            if "服务限频" in out:
                if attempt == 0:
                    time.sleep(40)
                    continue
                return None, "rate_limited"
            if r.returncode != 0:
                return None, f"rc_{r.returncode}"
            return _parse_markdown_table(out), "ok"
        except subprocess.TimeoutExpired:
            return None, "timeout"
        except Exception as e:
            return None, f"err_{type(e).__name__}"
    return None, "unknown"


def repair(market: str, category: str, sleep: float = 1.0, dry_run: bool = False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    universe = set(Path(UNIVERSE / f"universe_{market}.txt").read_text(encoding="utf-8").split())
    got = existing_codes(market, category) | already_repaired(market, category)
    missing = sorted(universe - got)
    print(f"[{market}/{category}] universe={len(universe)} got={len(got)} missing={len(missing)}", flush=True)
    if dry_run or not missing:
        return missing
    log = LOG_DIR / f"{market}_{category}.jsonl"
    ok = fail = 0
    for i, code in enumerate(missing):
        df, status = fetch_one(code, category)
        if df is not None and not df.empty:
            with log.open("a", encoding="utf-8") as f:
                for _, row in df.iterrows():
                    rec = row.to_dict()
                    rec["code"] = code
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            ok += 1
        else:
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"code": code, "ok": False, "reason": status},
                                   ensure_ascii=False) + "\n")
            fail += 1
        if (i + 1) % 25 == 0 or i + 1 == len(missing):
            print(f"  {i+1}/{len(missing)} ok={ok} fail={fail}", flush=True)
        time.sleep(sleep)
    return missing


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="hs")
    ap.add_argument("--category", default="kline")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--all", action="store_true", help="补 quote/kline/finance 三类缺口")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.all:
        for m in ("hs", "hk", "us"):
            for c in ("quote", "kline", "finance"):
                repair(m, c, args.sleep, args.dry_run)
    else:
        repair(args.market, args.category, args.sleep, args.dry_run)
