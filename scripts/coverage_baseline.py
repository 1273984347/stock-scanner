#!/usr/bin/env python3
"""覆盖率基线工具 —— 按平台记录 + 回归门禁（RF-01 / Phase 0 剩余项 #2）

为什么**按平台**存基线（2026-11-07 CI 实测修正）：
  实测三平台 CI 覆盖率**完全相同**（linux/darwin/win32 均 43.77%）⇒ 平台相关分支
  目前尚未造成覆盖差异；但**本地 vs CI 差 0.68pp**（本地 win32 43.09% vs CI 43.77%）
  ⇒ 真正关键结论是：**基线必须来自门禁执行环境（CI），本地数字不可作基线**。
  按平台存是为将来出现平台专属测试、覆盖开始分化时仍成立；
  每个平台只与**自己的历史**比，避免跨平台误判。

用法：
  # 1) 跑测试并产出覆盖率 JSON（pytest-cov）
  pytest tests/ -q --cov=lib --cov=api --cov=config --cov=factors --cov=scanner \
         --cov=paper_trading --cov=trading_journal --cov=dashboard \
         --cov-report=json:coverage.json --cov-report=term

  # 2) 把当前结果写入基线（首次建立某平台时）
  python scripts/coverage_baseline.py --update --coverage-file coverage.json

  # 3) 与基线比对（CI 门禁）；下降超阈值则退出码 1
  python scripts/coverage_baseline.py --check --coverage-file coverage.json

输出为 ASCII（CI 日志跨平台编码坑：Windows stdout 默认 cp1252，中文会直接崩）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import platform
import sys
from datetime import datetime, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / "docs" / "refactor" / "coverage-baseline.json"

PLATFORM_MAP = {
    "win32": "win32",
    "linux": "linux",
    "darwin": "darwin",
}


def detect_platform() -> str:
    return PLATFORM_MAP.get(sys.platform, sys.platform)


def _norm(p: str) -> str:
    """归一化路径分隔符：Windows 上 coverage.json 用反斜杠，Linux 用正斜杠。"""
    return p.replace("\\", "/")


def read_coverage(path: pathlib.Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    totals = data.get("totals") or {}
    summary = totals.get("percent_covered", 0.0)
    modules = {}
    for fname, fdata in (data.get("files") or {}).items():
        s = fdata.get("summary") or {}
        modules[_norm(fname)] = {
            "pct": round(float(s.get("percent_covered", 0.0)), 2),
            "statements": int(s.get("num_statements", 0)),
            "covered_lines": int(s.get("covered_lines", 0)),
        }
    return {
        "total_pct": round(float(summary), 2),
        "total_statements": int(totals.get("num_statements", 0)),
        "covered_lines": int(totals.get("covered_lines", 0)),
        "modules": modules,
    }


def _load_baseline(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "platforms": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_baseline(path: pathlib.Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def do_update(args) -> int:
    cov = read_coverage(pathlib.Path(args.coverage_file))
    plat = args.platform or detect_platform()
    doc = _load_baseline(pathlib.Path(args.baseline))
    doc["schema_version"] = 1
    doc["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc.setdefault("platforms", {})[plat] = cov
    _save_baseline(pathlib.Path(args.baseline), doc)
    print(f"[OK] baseline updated for platform '{plat}': "
          f"total {cov['total_pct']}% ({cov['covered_lines']}/{cov['total_statements']} lines), "
          f"{len(cov['modules'])} modules -> {args.baseline}")
    return 0


def do_check(args) -> int:
    cov = read_coverage(pathlib.Path(args.coverage_file))
    plat = args.platform or detect_platform()
    doc = _load_baseline(pathlib.Path(args.baseline))
    base = (doc.get("platforms") or {}).get(plat)

    if not base:
        # 首次建立该平台的基线：只报告，不阻断（否则 CI 永远红）
        print(f"[INFO] no baseline for platform '{plat}' yet -> reporting only "
              f"(current total {cov['total_pct']}%, {len(cov['modules'])} modules).")
        print("       Run: python scripts/coverage_baseline.py --update "
              "--coverage-file <file>   to establish it.")
        return 0

    cur_total = cov["total_pct"]
    base_total = base["total_pct"]
    delta_total = round(cur_total - base_total, 2)

    regressions = []
    base_modules = base.get("modules") or {}
    for name, m in cov["modules"].items():
        b = base_modules.get(name)
        if not b:
            continue
        d = round(m["pct"] - b["pct"], 2)
        if d < -args.module_threshold:
            regressions.append((name, b["pct"], m["pct"], d))
    regressions.sort(key=lambda x: x[3])

    print(f"[INFO] platform={plat}  total {cur_total}% (baseline {base_total}%, delta {delta_total:+}%)")

    if delta_total < -args.threshold:
        print(f"[FAIL] total coverage dropped {abs(delta_total)}pp "
              f"(threshold -{args.threshold}pp).")
        for name, b, c, d in regressions[:20]:
            print(f"       {d:+7.2f}pp  {name}  ({b}% -> {c}%)")
        return 1

    if regressions:
        print(f"[FAIL] {len(regressions)} module(s) regressed beyond "
              f"-{args.module_threshold}pp:")
        for name, b, c, d in regressions[:20]:
            print(f"       {d:+7.2f}pp  {name}  ({b}% -> {c}%)")
        return 1

    print(f"[PASS] coverage OK for '{plat}' (total {cur_total}%, baseline {base_total}%).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="coverage baseline (per-platform) / gate")
    ap.add_argument("--coverage-file", required=True, help="coverage JSON produced by pytest-cov")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--platform", default=None, help="override platform key (default: detect)")
    ap.add_argument("--update", action="store_true", help="write current result into baseline")
    ap.add_argument("--check", action="store_true", help="compare against baseline (gate)")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="max allowed total coverage drop in percentage points")
    ap.add_argument("--module-threshold", type=float, default=2.0,
                    help="max allowed per-module coverage drop in percentage points")
    args = ap.parse_args(argv)

    if args.update:
        return do_update(args)
    if args.check:
        return do_check(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
