#!/usr/bin/env python3
"""取历史时点 T0 的 iFinD Sharpe 指标（严格前瞻验证用）

关键：iFinD 自然语言对日期格式敏感——
- "2026-05-26" 被解析为起止同日 ⇒ Sharpe 空
- "20260526"   被解析为 20250526→20260526（一年窗口）⇒ Sharpe 有效
本脚本统一用紧凑格式 YYYYMMDD，并校验 indicators_params 的截止日期，
口径不符（起止同日/缺字段）的样本标记 invalid 不参与分组。

输出：cache/ifind/*_historical_sharpe_{T0}.json，含原始帧与口径参数。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "ifind"
sys.path.insert(0, str(ROOT))


def _call(tool: str, params: dict, interval: float) -> dict:
    skill = Path(os.environ.get("IFIND_SKILL_DIR",
                                Path.home() / ".workbuddy" / "skills" / "ifind-finance-data"))
    spec = importlib.util.spec_from_file_location("_ifind_call", skill / "call.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    time.sleep(interval)
    res = mod.call("stock", tool, params)
    if not isinstance(res, dict) or res.get("ok") is False:
        raise RuntimeError(f"{tool} failed: {res.get('error') if isinstance(res, dict) else res}")
    return res


def _inner(res: dict) -> dict:
    payload = res.get("data", {}).get("result", {})
    for item in payload.get("content", []):
        try:
            return json.loads(item["text"])["data"]
        except (KeyError, ValueError, TypeError):
            continue
    return {}


def _parse_sharpe(answer: str, code: str) -> float | None:
    lines = [ln for ln in answer.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 3:
        return None
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        row = dict(zip(header, cells))
        cell = next((v for k, v in row.items() if "代码" in k), None)
        if cell and cell.split(".")[0] == code:
            val = next((v for k, v in row.items() if "Sharpe" in k), None)
            try:
                return float(val) if val else None
            except ValueError:
                return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--t0", default="20260526", help="历史时点，紧凑格式 YYYYMMDD")
    ap.add_argument("--t1", default="", help="窗口起点，紧凑格式；不传则自动取 t0 一年前")
    ap.add_argument("--size", type=int, default=220, help="样本数")
    ap.add_argument("--interval", type=float, default=0.6, help="调用间隔秒")
    args = ap.parse_args()

    t1 = args.t1 or f"{int(args.t0[:4]) - 1}{args.t0[4:]}"
    if len(t1) != 8 or len(args.t0) != 8:
        raise SystemExit("--t0/--t1 必须是 YYYYMMDD 八位紧凑格式")

    dist_files = sorted(CACHE.glob("*_factor_distribution_filled.json"))
    if not dist_files:
        dist_files = sorted(CACHE.glob("*_factor_distribution.json"))
    dist = json.loads(dist_files[-1].read_text(encoding="utf-8"))
    codes = [d["code"] for d in dist["details"][:args.size]]
    print(f"T0={args.t0} / {len(codes)} 只逐只取历史 Sharpe", flush=True)

    records = []
    for i, code in enumerate(codes):
        # 显式给起止日期：单给截止日会被 iFinD 解析成起止同日 ⇒ Sharpe 空（实测有效率仅 2/5）
        q = f"{code}在{t1}到{args.t0}区间的夏普比率"
        raw_answer, params = "", {}
        try:
            data = _inner(_call("get_risk_indicators", {"query": q}, args.interval))
            raw_answer = data.get("answer", "")
            params = data.get("indicators_params", {})
            sharpe = _parse_sharpe(raw_answer, code)
        except Exception as exc:  # noqa: BLE001
            sharpe, params = None, {"error": str(exc)}

        sharpe_params = params.get("Sharpe", {}) if isinstance(params, dict) else {}
        start = str(sharpe_params.get("起始交易日期", ""))
        end = str(sharpe_params.get("截止交易日期", ""))
        valid = sharpe is not None and end == args.t0 and start == t1
        records.append({
            "code": code,
            "sharpe_t0": sharpe,
            "window_start": start,
            "window_end": end,
            "valid": valid,
            "raw_answer": raw_answer,
            "indicators_params": params,
        })
        if (i + 1) % 25 == 0:
            print(f"  进度 {i + 1}/{len(codes)}", flush=True)

    valid_n = sum(1 for r in records if r["valid"])
    print(f"有效样本 {valid_n}/{len(records)}")
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_historical_sharpe_{args.t0}.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "t0": args.t0,
        "t1": t1,
        "valid_count": valid_n,
        "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
