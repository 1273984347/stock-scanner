#!/usr/bin/env python3
"""pytest 耗时基线工具 —— 定位慢用例 + 稳定性基线（RF-01 / Phase 0）

两阶段用法：

  # 阶段 1：全量耗时排序（跑一次 pytest，解析 --durations）
  python scripts/pytest_duration_baseline.py --collect --out docs/refactor/test-duration-baseline.json

  # 阶段 2：稳定性基线（对最慢的 N 个用例重复跑 R 轮，算 min/median/max/CV）
  python scripts/pytest_duration_baseline.py --verify --repeat 3 --top 30 \
      --out docs/refactor/test-duration-baseline.json

为什么要有这个工具：
  本地全量 pytest 曾经 >28 min 而 CI 只要 110s（15 倍差），根因有两个：
    ① pytest.ini 的 log_cli=true + log_cli_level=DEBUG ⇒ DEBUG 日志双份打到终端
       （logging.conf 的 logger_quant level=DEBUG，handler 直写 stdout）
    ② conftest 只 mock 了 akshare 的 7 个接口，其余数据源走真实网络，
       且 lib/data_layer._retry_api_call 失败时 backoff 2s→4s（单次最坏 ~6s）
  本工具**一律关闭 log_cli**（-o log_cli=false）以排除日志 I/O 噪声，
  否则测出来的"慢"大半是日志而不是被测代码。

输出为 JSON（机器可读）+ 终端摘要（人可读），可直接 diff 两次运行的差异。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "refactor" / "test-duration-baseline.json"

# pytest --durations 的输出行： "12.34s call     tests/test_x.py::test_y"
_DUR_RE = re.compile(r"^([0-9.]+)s\s+(?:(call|setup|teardown)\s+)?(\S+)\s*$")
# 汇总行： "3 failed, 1117 passed, 12 skipped, 193 warnings in 110.69s (0:01:50)"
_SUM_RE = re.compile(r"=+\s*(.*?)\s*in\s+([0-9.]+)s.*=+", re.S)
# pytest 的 durations 块标题有两种形态（实测 pytest 8.4.2）：
#   --durations=0   -> "=========== slowest durations ============="
#   --durations=60  -> "=========== slowest 60 durations ============="
# 故用宽松匹配兼容两种。
_BLOCK_RE = re.compile(r"slowest\b.*\bdurations")

# 关闭实时 DEBUG 日志：排除日志 I/O 噪声（这是本地慢的主要放大器之一）
BASE_PYTEST_ARGS = [
    "-q", "--tb=no", "--durations=0",
    "-o", "log_cli=false",
    "-o", "log_cli_level=WARNING",
]


def _run_pytest(args, timeout=3600):
    cmd = [sys.executable, "-m", "pytest", *args]
    env = dict(os.environ)
    env.setdefault("SKIP_HS300_PREFETCH", "1")
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def parse_durations(text):
    """解析 pytest 输出，返回 ({nodeid: seconds}, summary_dict)。"""
    durations, summary = {}, {}
    in_block = False
    for line in text.splitlines():
        if _BLOCK_RE.search(line):
            in_block = True
            continue
        if in_block and line.startswith("=") and "short test summary" in line:
            in_block = False
        if in_block:
            m = _DUR_RE.match(line.strip())
            if m:
                durations[m.group(3)] = float(m.group(1))
        if "passed" in line and (" in " in line) and line.strip().startswith("="):
            sm = _SUM_RE.search(line)
            if sm:
                summary["raw"] = sm.group(1).strip()
                summary["seconds"] = float(sm.group(2))
    return durations, summary


def _env_fingerprint():
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "tdx_data_path": os.environ.get("TDX_DATA_PATH", "<unset>"),
        "cwd": str(ROOT),
    }


def do_scan_by_file(args):
    """逐文件跑 pytest，产出文件级耗时排序（覆盖全部测试文件）。

    为什么要逐文件：一次全量只能给出"总时长"，无法归因到文件；
    逐文件跑能把慢区收敛到具体文件，且每个文件的用例级 durations 一并拿到。
    """
    test_files = sorted(str(p.relative_to(ROOT)).replace("\\", "/")
                        for p in (ROOT / "tests").glob("test_*.py"))
    files = []
    all_durations = {}
    total = 0.0
    for i, rel in enumerate(test_files, 1):
        rc, text = _run_pytest([*BASE_PYTEST_ARGS, rel])
        durations, summary = parse_durations(text)
        wall = summary.get("seconds") or sum(durations.values())
        total += wall
        files.append({"file": rel, "wall_seconds": round(wall, 2),
                      "measured": len(durations)})
        for n, s in durations.items():
            all_durations[n] = s
        print(f"  [{i:>3}/{len(test_files)}] {wall:7.2f}s  {rel}")
        sys.stdout.flush()

    files.sort(key=lambda x: x["wall_seconds"], reverse=True)
    ranked = sorted(all_durations.items(), key=lambda kv: kv[1], reverse=True)

    print(f"[OK] scanned {len(files)} files, total {total:.1f}s")
    print("     --- top 15 slowest files ---")
    for f in files[:15]:
        print(f"     {f['wall_seconds']:8.2f}s  {f['file']}")
    print("     --- top 15 slowest tests ---")
    for n, s in ranked[:15]:
        print(f"     {s:8.2f}s  {n}")

    doc = _load(args.out)
    doc.setdefault("schema_version", 1)
    doc["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["env"] = _env_fingerprint()
    doc["scan_by_file"] = {"files": len(files), "total_seconds": round(total, 2),
                           "ranked": files}
    doc["durations"] = [{"nodeid": n, "seconds": round(s, 3)} for n, s in ranked]
    _save(args.out, doc)
    print(f"[OK] written to {args.out}")
    return 0


def do_collect(args):
    targets = args.files.split(",") if args.files else ["tests/"]
    rc, text = _run_pytest([*BASE_PYTEST_ARGS, *targets])
    durations, summary = parse_durations(text)
    if not durations:
        print("[FAIL] could not parse durations from pytest output", file=sys.stderr)
        print(text[-2000:], file=sys.stderr)
        return 1

    ranked = sorted(durations.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(durations.values())
    top = ranked[: args.top]

    print(f"[OK] collected {len(durations)} measured tests, total {total:.1f}s "
          f"(wall {summary.get('seconds', 0):.1f}s)")
    print(f"     summary: {summary.get('raw', '?')}")
    print("     --- top 15 slowest ---")
    for nodeid, sec in top[:15]:
        print(f"     {sec:8.2f}s  {nodeid}")

    doc = _load(args.out)
    doc.setdefault("schema_version", 1)
    doc["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["env"] = _env_fingerprint()
    doc["collect"] = {
        "pytest_args": " ".join(BASE_PYTEST_ARGS + targets),
        "targets": targets,
        "measured_tests": len(durations),
        "sum_seconds": round(total, 3),
        "wall_seconds": summary.get("seconds"),
        "summary": summary.get("raw"),
    }
    doc["durations"] = [{"nodeid": n, "seconds": round(s, 3)} for n, s in ranked]
    _save(args.out, doc)
    print(f"[OK] written to {args.out}")
    return 0


def do_verify(args):
    doc = _load(args.out)
    if not doc.get("durations"):
        print("[FAIL] no durations in baseline; run --collect first", file=sys.stderr)
        return 1

    top_items = doc["durations"][: args.top]
    nodeids = [d["nodeid"] for d in top_items]
    print(f"[OK] re-running {len(nodeids)} slowest tests, {args.repeat} round(s)")

    per_test = {n: [] for n in nodeids}
    rounds = []
    for i in range(args.repeat):
        rc, text = _run_pytest([*BASE_PYTEST_ARGS, *nodeids])
        durations, summary = parse_durations(text)
        hit = [n for n in nodeids if n in durations]
        for n in hit:
            per_test[n].append(durations[n])
        rounds.append({"round": i + 1, "measured": len(hit), "wall_seconds": summary.get("seconds")})
        print(f"     round {i+1}/{args.repeat}: measured {len(hit)}/{len(nodeids)}, "
              f"wall {summary.get('seconds')}s")

    stability = []
    for n, vals in per_test.items():
        if not vals:
            continue
        mean = statistics.fmean(vals)
        std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        stability.append({
            "nodeid": n,
            "runs": len(vals),
            "min": round(min(vals), 3),
            "median": round(statistics.median(vals), 3),
            "max": round(max(vals), 3),
            "mean": round(mean, 3),
            "std": round(std, 3),
            # CV 在 2-3 轮的小样本下估计不可靠（同一用例 3 轮 CV=0.30 / 2 轮 CV=0.17）；
            # max/min 比值不依赖方差估计，是更稳健的抖动指标。
            "cv": round(std / mean, 4) if mean else None,
            "max_min_ratio": round(max(vals) / min(vals), 3) if min(vals) else None,
        })
    stability.sort(key=lambda x: x["median"], reverse=True)

    # 合并语义：按 nodeid 更新，保留此前测过的其它条目
    prev_items = {}
    for it in (doc.get("stability") or {}).get("items", []):
        prev_items[it["nodeid"]] = it
    for it in stability:
        prev_items[it["nodeid"]] = it
    merged = sorted(prev_items.values(), key=lambda x: x.get("median", 0), reverse=True)
    doc["stability"] = {
        "repeat": args.repeat,
        "top_n": args.top,
        "rounds": rounds,
        "items": merged,
    }
    doc["stability_generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save(args.out, doc)

    print(f"[OK] stability written to {args.out}")
    print("     --- median / cv (cv = std/mean; >0.3 means unstable) ---")
    for s in merged[:15]:
        cv = s["cv"]
        print(f"     median {s['median']:8.3f}s  cv {cv if cv is not None else 'n/a'}  {s['nodeid']}")
    return 0


def _load(path):
    p = pathlib.Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _save(path, doc):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="pytest duration / stability baseline")
    ap.add_argument("--collect", action="store_true", help="full run, parse --durations")
    ap.add_argument("--verify", action="store_true", help="re-run slowest N tests, compute stability")
    ap.add_argument("--top", type=int, default=30, help="how many slowest tests to keep / verify")
    ap.add_argument("--repeat", type=int, default=3, help="rounds for --verify")
    ap.add_argument("--scan-by-file", action="store_true",
                    help="run each test file separately, produce per-file ranking")
    ap.add_argument("--files", default=None,
                    help="comma-separated test files/dirs to measure (default: whole tests/)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    if args.scan_by_file:
        return do_scan_by_file(args)
    if args.collect:
        return do_collect(args)
    if args.verify:
        return do_verify(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
