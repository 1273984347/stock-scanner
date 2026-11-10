#!/usr/bin/env python
"""开关前后响应样例 diff —— RF-01 Phase 4 阶段 B 的退出判据取证工具。

计划 §三 Phase 4 退出判据要求：「开关切换前后响应样例 diff = 0」，
且「端点响应结构不变（评分字段名、数值语义、等级映射均冻结）」。

做法：在**同一进程**里，用 `dual_run_diff.py` 的 pin 环境注入固定数据，
分别在 `FEATURE_UNIFIED_SCORING=off` 与 on 侧取值下调用
`pipeline.get_stock_score()`，对整份响应逐字段比对（容差 1e-9）。
同进程 + 同一份注入数据 ⇒ 不存在跨进程/跨环境假红。

**两条评分分支都要覆盖**（阶段 B 第 2 批起）：
  - `realtime`   : rt_row 非空 -> score_with_realtime -> strategies 消费者
  - `no_realtime`: rt_row 置空 -> score_without_realtime -> pipeline 消费者
`no_realtime` 分支在 pin 上下文内把 `fetch_stock_data` 的返回改成
`(hist, None, fin_data_dict, idx_df)`，从而进入真实的无实时行情路径。

**自证有效性**（关键）：如果 on 侧根本没走网关，两边都跑旧实现，
diff 也会是 0 ⇒ 空判据（坑表 P-56）。因此本工具 patch
`scoring_adapters.build_kernel` 计数，断言 off 时内核调用为 0；
当某个分支的消费者**被该 flag 命中**时，还要求 on 侧内核调用 > 0。
未被命中的分支只报 `[INFO]`，其 diff 不作为灰度证据。

用法：
  python scripts/scoring_switch_diff.py --check
  python scripts/scoring_switch_diff.py --check --flag strategies
  python scripts/scoring_switch_diff.py --check --branch no_realtime
  python scripts/scoring_switch_diff.py --summary
输出为纯 ASCII（Windows CI stdout 默认 cp1252）。
"""

from __future__ import annotations

import argparse
import importlib.util
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
BRANCHES = ("realtime", "no_realtime")
BRANCH_CONSUMER = {"realtime": "strategies", "no_realtime": "pipeline"}


def _load_dual_run():
    spec = importlib.util.spec_from_file_location(
        "_dual_run_for_switch", ROOT / "scripts" / "dual_run_diff.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _capture(flag_value: str, drd, branch: str) -> dict:
    """在指定 flag + 指定评分分支下采集各股票的完整 get_stock_score 响应。"""
    os.environ["FEATURE_UNIFIED_SCORING"] = flag_value
    import lib.strategy.steps as steps

    out = {}
    with drd.pinned_environment():
        with drd._inject_shared_data(CODES):
            orig_fetch = steps.fetch_stock_data
            if branch == "no_realtime":
                def fetch_no_rt(code, days=180):
                    hist, _rt, _fin, idx = orig_fetch(code, days)
                    # 用 dict 形态的 fin_data，顺带覆盖 score_without_realtime
                    # 的 tdx_fundamentals / pe / ocf 分支
                    fin = {
                        "pe": 20.0,
                        "gross_margin": 0.4,
                        "operating_cash_flow": 1e8,
                        "tdx_fundamentals": {
                            "profitability": {"ROE": 0.2, "gross_margin": 0.45},
                            "solvency": {"debt_to_asset": 0.3},
                            "growth": {"net_income_growth": 0.25},
                        },
                    }
                    return hist, None, fin, idx
                steps.fetch_stock_data = fetch_no_rt
            try:
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
            finally:
                steps.fetch_stock_data = orig_fetch
    return out


def _targeted(flag_value: str, consumer: str) -> bool:
    """该 flag 取值是否命中这个消费者（用生产判定函数，不复制逻辑）。"""
    from config import unified_scoring_enabled
    old = os.environ.get("FEATURE_UNIFIED_SCORING")
    os.environ["FEATURE_UNIFIED_SCORING"] = flag_value
    try:
        return unified_scoring_enabled(consumer)
    finally:
        if old is None:
            os.environ.pop("FEATURE_UNIFIED_SCORING", None)
        else:
            os.environ["FEATURE_UNIFIED_SCORING"] = old


def main() -> int:
    global CODES
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="run off/on diff (default)")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--flag", default="all",
                    help="on-side flag value (default: all; also 'strategies' / 'pipeline')")
    ap.add_argument("--branch", choices=("both",) + BRANCHES, default="both")
    ap.add_argument("--codes", default=",".join(CODES))
    args = ap.parse_args()

    from lib.scoring.scoring_gateway import GRAY_CONSUMERS
    branches = BRANCHES if args.branch == "both" else (args.branch,)
    if args.summary:
        print(f"[INFO] gray consumers: {', '.join(GRAY_CONSUMERS)}")
        print(f"[INFO] branches: {', '.join(branches)}")
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
    results = []
    failures = []
    try:
        adapters.build_kernel = counting_build
        for branch in branches:
            consumer = BRANCH_CONSUMER[branch]

            counter[0] = 0
            off = _capture("off", drd, branch)
            off_calls = counter[0]

            counter[0] = 0
            on = _capture(args.flag, drd, branch)
            on_calls = counter[0]

            if off_calls != 0:
                failures.append(f"{branch}: off-side called kernel {off_calls} time(s)")
                continue

            targeted = _targeted(args.flag, consumer)
            if targeted and on_calls == 0:
                failures.append(
                    f"{branch}: on-side never reached ScoringKernel -> "
                    f"diff would be vacuous (P-56)")
                continue
            if not targeted:
                print(f"[INFO] branch {branch}: consumer '{consumer}' not targeted "
                      f"by flag '{args.flag}'; diff is informational only")

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
                failures.extend(f"{branch}: {d}" for d in diffs)
            else:
                results.append((branch, consumer, on_calls))
                print(f"[PASS] branch {branch}: 0 field(s) differ "
                      f"({len(CODES)} codes, off vs {args.flag}; "
                      f"kernel calls off=0 on={on_calls}, consumer={consumer})")
    finally:
        adapters.build_kernel = orig_build
        if old_env is None:
            os.environ.pop("FEATURE_UNIFIED_SCORING", None)
        else:
            os.environ["FEATURE_UNIFIED_SCORING"] = old_env

    if failures:
        print(f"[FAIL] switch diff: {len(failures)} problem(s)")
        for f in failures[:30]:
            print("      " + f)
        return 1

    print(f"[PASS] switch diff: {len(results)} branch(es), "
          f"{len(results) * len(CODES)} responses, 0 field(s) differ "
          f"(off vs {args.flag})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
