"""后台拉取任务健康巡检：三要素判据（进程 / 产物增长 / 数据源探活）。

判断"卡住"必须三者结合——只看进程会把 sleep 退避误判为正常运行。
用法：
    python scripts/check_task_health.py                # 单次快照
    python scripts/check_task_health.py --watch 60     # 间隔60秒看两次，对比增长
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QFQ = ROOT / "data" / "exports" / "bulk" / "_qfq" / "hs_qfq.jsonl"
QFQ_FAILED = ROOT / "data" / "exports" / "bulk" / "_qfq" / "hs_qfq_failed.jsonl"
PROBE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,3,qfq"


def qfq_codes() -> tuple:
    if not QFQ.exists():
        return 0, 0
    codes = set()
    rows = 0
    for line in QFQ.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            import json
            codes.add(json.loads(line)["code"])
            rows += 1
        except Exception:
            pass
    return len(codes), rows


def python_procs() -> int:
    """用 Windows CIM 列全部 python/westock 进程（P-138：Git Bash ps 看不到独立 worker）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|westock)(\\.exe)?$' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        return int(out) if out.isdigit() else -1
    except Exception:
        return -1


def datasource_alive() -> bool:
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", "10", PROBE_URL],
            capture_output=True, text=True, timeout=15,
        ).stdout
        return not out.strip().startswith("<!DOCTYPE") and '"code":0' in out
    except Exception:
        return False


def snapshot(tag: str):
    codes, rows = qfq_codes()
    procs = python_procs()
    alive = datasource_alive()
    print(f"[{tag}] python_procs={procs} qfq_codes={codes} rows={rows:,} datasource={'OK' if alive else 'WAF_DOWN'}")
    return codes, rows, procs, alive


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="间隔秒数，对比两次快照")
    args = ap.parse_args()
    a = snapshot("now")
    if args.watch:
        time.sleep(args.watch)
        b = snapshot(f"+{args.watch}s")
        delta = b[0] - a[0]
        print("\n=== 判定 ===")
        if b[2] <= 0:
            print("[STOPPED] 无 python 进程 —— 任务已中断，需重启（断点续传会跳过已完成）")
        elif delta == 0:
            if not b[3]:
                print("[WAF_BLOCKED] 进程在但零产出 + 数据源 WAF —— 脚本在退避，属外部封禁，不是死锁")
            else:
                print("[STUCK] 进程在、数据源正常、但零产出 —— 疑似真卡住，需查日志/重启")
        else:
            print(f"[RUNNING] 正常推进：{args.watch}s 内 +{delta} 只")
    else:
        print("\n提示：加 --watch 60 对比增长，才能区分'慢'与'卡'")


if __name__ == "__main__":
    main()
