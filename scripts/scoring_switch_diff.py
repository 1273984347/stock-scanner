#!/usr/bin/env python
"""开关前后响应样例 diff —— RF-01 Phase 4 阶段 B 的退出判据取证工具。

计划 §三 Phase 4 退出判据要求：「开关切换前后响应样例 diff = 0」，
且「端点响应结构不变（评分字段名、数值语义、等级映射均冻结）」。

做法：在**同一进程**里，用 `dual_run_diff.py` 的 pin 环境注入固定数据，
分别在 `FEATURE_UNIFIED_SCORING=off` 与 `=strategies`（或 `all`）下调用
`pipeline.get_stock_score()`，对整份响应逐字段比对（容差 1e-9）。
同进程 + 同一份注入数据 ⇒ 不存在跨进程/跨环境假红。

**自证有效性**（关键）：如果 on 分支根本没走网关，两边都跑旧实现，
diff 也会是 0 ⇒ 空判据（坑表 P-54）。因此本工具额外 patch
`scoring_adapters.build_kernel` 计数，断言 on 时内核确实被调用、off 时为 0。

用法：
  python scripts/scoring_switch_diff.py --check
  python scripts/scoring_switch_diff.py --check --flag all
  python scripts/scoring_switch_diff.py --summary
输出为纯 ASCII（Windows CI stdout 默认 cp1252）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CODES = ["600519", "000858", "600036"]


def _load_dual_run():
    spec = importlib.util.spec_from_file_location(
        "_dual_run_for_switch", ROOT / "scripts" / "dual_run_diff.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _capture(flag_value: str, drd, counting=None) -> dict:
    """在指定 flag 下采集 3 只股票的完整 get_stock_score 响应。"""
    os.environ["FEATURE_UNIFIED_SCORING"] = flag_value
    out = {}
    with drd.pinned_environment():
        with drd._inject_shared_data(CODES):
            for code in CODES:
                try:
                    out[code] = {
                        "ok": True,
                        "result": drd.normalize(drd.capture_score(code)),
                    }
                except Exception as exc:  # noqa: BLE001
                    out[code] = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
    return out


def main() -> int:
    global CODES
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="run off/on diff (default)")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--flag", default="strategies",
                    help="on-side flag value (default: strategies; use 'all' for global)")
    ap.add_argument("--codes", default=",".join(CODES))
    args = ap.parse_args()

    from lib.scoring.scoring_gateway import GRAY_CONSUMERS
    if args.summary:
        print(f"[INFO] gray consumers: {', '.join(GRAY_CONSUMERS)}")
        print(f"[INFO] codes: {args.codes}")
        print(f"[INFO] on-side flag: FEATURE_UNIFIED_SCORING={args.flag}")
        return 0

    CODES = [c.strip() for c in args.codes.split(",") if c.strip()]
    drd = _load_dual_run()
    import lib.scoring.scoring_adapters as adapters

    orig_build = adapters.build_kernel
    counter = [0]

    def counting_build():
        counter[0] += 1
        return orig_build()

    old_env = os.environ.get("FEATURE_UNIFIED_SCORING")
    try:
        # ---- off 侧：内核调用数必须为 0 ----
        adapters.build_kernel = counting_build
        off = _capture("off", drd)
        off_calls = counter[0]
        if off_calls != 0:
            print(f"[FAIL] off-side unexpectedly called kernel {off_calls} time(s)")
            return 1

        # ---- on 侧：内核调用数必须 > 0（自证有效性）----
        counter[0] = 0
        on = _capture(args.flag, drd)
        on_calls = counter[0]
    finally:
        adapters.build_kernel = orig_build
        if old_env is None:
            os.environ.pop("FEATURE_UNIFIED_SCORING", None)
        else:
            os.environ["FEATURE_UNIFIED_SCORING"] = old_env

    if on_calls == 0:
        print("[FAIL] on-side never reached ScoringKernel -> diff would be vacuous (P-54)")
        return 1

    diffs = []
    for code in CODES:
        if not off[code]["ok"] or not on[code]["ok"]:
            diffs.append(f"{code}: capture error "
                         f"{off[code].get('error') or on[code].get('error')}")
            continue
        for d in drd.compare(off[code]["result"], on[code]["result"], code):
            diffs.append(f"{code}.{d['path']}: {d['old']!r} -> {d['new']!r}"
                         if "old" in d else f"{code}: {d}")

    if diffs:
        print(f"[FAIL] switch diff: {len(diffs)} difference(s)")
        for d in diffs[:30]:
            print("      " + d)
        return 1

    print(f"[PASS] switch diff: 0 field(s) differ "
          f"({len(CODES)} codes, off vs {args.flag}; "
          f"kernel calls off={off_calls} on={on_calls})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
