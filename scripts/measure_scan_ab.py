"""盘中 A/B 实测：fund_flow 双缓存 + 熔断（ADR-0016）的端到端收益。

背景：TODO L281「扫描性能：300+ 股 5+ 分钟」的根因修（3f2bf85 双缓存）与
负路径治理（ADR-0016 熔断）都有单测锁定，但端到端收益一直未在真实扫描复测——
2026-11-14 凌晨实测时东方财富资金流端点拒连，全零按设计不缓存，暖缓存收益无法
验证。本脚本在盘中（9:30-15:00 候选池与历史基线同构）跑两轮真实扫描：

  第 1 轮（冷）：清空 fundflow 磁盘缓存 → 全量扫描 → 记录墙钟 T1
  第 2 轮（暖）：fund_flow 1h TTL 内立即重跑 → 记录墙钟 T2
  对照：T2 vs T1 vs 历史基线「300+ 股 5+ 分钟」；同时统计熔断触发与失败日志，
  顺带验证 ADR-0016 熔断在真实故障下的行为（若上游仍拒连）。

用法：
    python scripts/measure_scan_ab.py            # 冷 + 暖两轮
    python scripts/measure_scan_ab.py --runs 1   # 只跑一轮（冷）
    python scripts/measure_scan_ab.py --keep-cache  # 不清缓存（纯暖跑）

只读缓存目录与日志文件，不改任何业务状态；run.py 用 --no-push 不推送飞书。
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUND_CACHE = ROOT / "cache" / "fundflow"
LOG_PATH = ROOT / "cache" / "measure_scan_ab.log"


def count_fundflow_entries() -> int:
    return len(list(FUND_CACHE.glob("*"))) if FUND_CACHE.exists() else 0


def run_scan(tag: str) -> dict:
    """跑一轮全量扫描，返回墙钟 / 退出码 / 关键日志计数。"""
    t0 = time.time()
    with open(LOG_PATH, "w", encoding="utf-8", errors="replace") as f:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "run.py"), "--no-push", "--preset", "balanced"],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, timeout=3600,
        )
    wall = time.time() - t0
    text = LOG_PATH.read_text(encoding="utf-8", errors="replace").replace("\r", "\n")
    return {
        "tag": tag,
        "wall_s": round(wall, 1),
        "exit": proc.returncode,
        "fundflow_entries": count_fundflow_entries(),
        "breaker_trips": len(re.findall(r"已熔断", text)),
        "source_failures": len(re.findall(r"全源失败|资金流向获取失败|akshare 资金流向失败", text)),
        "scan_done": "共" in text and "候选股" in text,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", type=int, default=2, choices=(1, 2),
                        help="轮数：1=只冷跑，2=冷+暖（默认）")
    parser.add_argument("--keep-cache", action="store_true",
                        help="不清 fundflow 磁盘缓存（纯暖跑对比）")
    args = parser.parse_args()

    if not args.keep_cache and args.runs == 2:
        if FUND_CACHE.exists():
            n_before = count_fundflow_entries()
            shutil.rmtree(FUND_CACHE)
            print(f"[prep] 已清空 fundflow 磁盘缓存（清出 {n_before} 条，确保第 1 轮为冷跑）")

    results = []
    for i in range(1, args.runs + 1):
        tag = "冷(缓存已清)" if (i == 1 and not args.keep_cache) else "暖(1h TTL 内)"
        print(f"\n[run {i}/{args.runs}] {tag} —— 扫描中，请稍候…")
        r = run_scan(tag)
        results.append(r)
        print(f"    墙钟 {r['wall_s']}s | 退出码 {r['exit']} | fundflow 缓存 {r['fundflow_entries']} 条 | "
              f"熔断触发 {r['breaker_trips']} 次 | 源失败日志 {r['source_failures']} 行")

    print("\n===== 汇总 =====")
    for r in results:
        print(f"  {r['tag']:<12} 墙钟 {r['wall_s']:>7.1f}s  fundflow {r['fundflow_entries']:>4} 条")
    if len(results) == 2 and results[0]["wall_s"] > 0:
        t1, t2 = results[0]["wall_s"], results[1]["wall_s"]
        print(f"  暖/冷墙钟比 T2/T1 = {t2 / t1:.2f}（历史基线：300+ 股 5+ 分钟 = 300s+，"
              f"双缓存 + 熔断后的暖跑应显著低于 300s）")
    print(f"  详细日志：{LOG_PATH}")


if __name__ == "__main__":
    main()
