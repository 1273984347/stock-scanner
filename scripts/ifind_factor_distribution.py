#!/usr/bin/env python3
"""HS300 样本 ESG / 风险因子分布分析（一次性取证脚本，可复跑）

目的：为"是否把 iFinD ESG/风险因子纳入 ic_score"提供统计基础（ADR-0037 取证）。
成本：50 只 × 2 指标，按每批 5 主体合并查询 -> 约 20 次调用（免费版 2 QPS 限流）。

输出：cache/ifind/YYYYMMDD_HHMMSS_factor_distribution.json
      含每只股票明细 + 分布统计（ESG 等级计数、Sharpe/回撤/波动率分位数）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "cache" / "ifind"
sys.path.insert(0, str(ROOT))


def _call(server: str, tool: str, params: dict, interval: float) -> dict:
    """调用 skill 的 call.py，带 2 QPS 限流。"""
    import importlib.util
    import os

    skill = Path(os.environ.get("IFIND_SKILL_DIR",
                                Path.home() / ".workbuddy" / "skills" / "ifind-finance-data"))
    spec = importlib.util.spec_from_file_location("_ifind_call", skill / "call.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    time.sleep(interval)
    res = mod.call(server, tool, params)
    if not isinstance(res, dict) or res.get("ok") is False:
        raise RuntimeError(f"{tool} failed: {res.get('error') if isinstance(res, dict) else res}")
    return res


def _answer(res: dict) -> str:
    payload = res.get("data", {}).get("result", {})
    for item in payload.get("content", []):
        try:
            inner = json.loads(item["text"])["data"]
            return inner.get("answer", "")
        except (KeyError, ValueError, TypeError):
            continue
    return ""


def _rows(answer: str) -> list[dict[str, str]]:
    """按表头定位列，返回 dict。列顺序在批次间会翻转（实测），必须按表头取值。"""
    lines = [ln for ln in answer.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 3:
        return []
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    out = []
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(header):
            out.append(dict(zip(header, cells)))
    return out


def _pick(row: dict[str, str], *tokens: str) -> str | None:
    """按同义词 token 取值：命中多个列时取最精确者，避免子串误匹配。"""
    for token in tokens:  # 调用方按优先级排序
        for k, v in row.items():
            if token in k:
                return v
    return None


def _code_of(row: dict[str, str]) -> str:
    return (_pick(row, "证券代码", "代码") or "").split(".")[0]


def get_hs300_codes(interval: float) -> list[str]:
    """取 HS300 全量成分。

    iFinD index.sector_data 单次只返回前 100 行且对修饰 query 无感（实测），
    因此改用 akshare 中证指数官方成分表（300 只，带当日日期），
    失败再回退 iFinD（仅 100 只，脚本会显式提示样本受限）。
    """
    import akshare as ak
    try:
        df = ak.index_stock_cons_csindex(symbol="000300")
        col = next(c for c in df.columns if "成分券代码" in c)
        codes = [str(x).zfill(6) for x in df[col].tolist()]
        print(f"  [universe] akshare 中证官方成分 {len(codes)} 只", flush=True)
        return codes
    except Exception as exc:  # noqa: BLE001
        print(f"  [universe] akshare 失败({exc})，回退 iFinD（仅100只）", file=sys.stderr)

    ans = _answer(_call("index", "sector_data", {
        "query": "沪深300指数的所有成分股代码和名称"
    }, interval))
    codes = []
    for row in _rows(ans):
        cell = next((v for k, v in row.items() if "代码" in k), None)
        if cell:
            code = cell.split(".")[0]
            if len(code) == 6 and code not in codes:
                codes.append(code)
    return codes


def batch_query(codes: list[str], tool: str, query_tpl: str, interval: float) -> tuple[list[dict[str, str]], list[str]]:
    """批量取数。返回 (行列表, 原始 answer 列表)——原始帧必须留证，否则口径漂移无法复现。"""
    all_rows: list[dict[str, str]] = []
    raw_answers: list[str] = []
    batch_size = 5  # skill 通用上限：主体数 <= 5
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        q = query_tpl.format(names="、".join(batch))
        try:
            answer = _answer(_call("stock", tool, {"query": q}, interval))
            rows = _rows(answer)
            raw_answers.append(f"[batch {i // batch_size + 1}] {q}\n{answer}")
            all_rows.extend(rows)
            print(f"  {tool} batch {i // batch_size + 1}: {len(batch)} 只 -> {len(rows)} 行",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 - 单批失败不中断整体
            print(f"  {tool} batch {i // batch_size + 1} 失败: {exc}", file=sys.stderr)
    return all_rows, raw_answers


def _num(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {}
    s = sorted(values)
    n = len(s)

    def q(p: float) -> float:
        idx = min(int(p * (n - 1)), n - 1)
        return round(s[idx], 4)

    return {"min": round(s[0], 4), "p25": q(0.25), "median": q(0.5),
            "p75": q(0.75), "max": round(s[-1], 4), "n": n}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--size", type=int, default=50, help="HS300 取样数")
    ap.add_argument("--interval", type=float, default=0.6, help="调用间隔秒")
    args = ap.parse_args()

    print("取 HS300 成分股...", flush=True)
    codes = get_hs300_codes(args.interval)
    if not codes:
        raise RuntimeError("HS300 成分股取数为空（接口抖动或权益耗尽）——拒绝产出空分布")
    print(f"得到 {len(codes)} 只，取前 {min(args.size, len(codes))} 只", flush=True)
    codes = codes[:args.size]

    print("批量查 ESG...", flush=True)
    esg_rows, esg_raw = batch_query(codes, "get_esg_data", "{names}的ESG评级", args.interval)
    print(f"逐只查风险指标（{len(codes)} 次，规避多主体口径混淆）...", flush=True)
    risk_rows: list[dict[str, str]] = []
    risk_raw: list[str] = []
    for i, code in enumerate(codes):
        q = f"{code}的夏普比率、最大回撤、波动率"
        try:
            answer = _answer(_call("stock", "get_risk_indicators", {"query": q}, args.interval))
            rows = _rows(answer)
            risk_rows.extend(rows)
            risk_raw.append(f"[{code}] {answer}")
            if (i + 1) % 25 == 0:
                print(f"  风险指标进度 {i + 1}/{len(codes)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  {code} 风险取数失败: {exc}", file=sys.stderr)
    if not esg_rows or not risk_rows:
        raise RuntimeError("ESG 或风险批量取数为空——拒绝产出空分布")

    esg_by_code = {_code_of(r): r for r in esg_rows}
    risk_by_code = {_code_of(r): r for r in risk_rows}

    details = []
    for code in codes:
        e = esg_by_code.get(code, {})
        r = risk_by_code.get(code, {})
        details.append({
            "code": code,
            "esg_rating": _pick(e, "ESG评级"),
            "sharpe": _num(_pick(r, "Sharpe", "夏普")),
            "max_drawdown_pct": _num(_pick(r, "最大回撤")),
            "volatility_pct": _num(_pick(r, "波动率")),
        })

    from collections import Counter
    esg_dist = dict(Counter(d["esg_rating"] for d in details if d["esg_rating"]))
    stats = {
        "sharpe": _quantiles([d["sharpe"] for d in details if d["sharpe"] is not None]),
        "max_drawdown_pct": _quantiles([d["max_drawdown_pct"] for d in details
                                        if d["max_drawdown_pct"] is not None]),
        "volatility_pct": _quantiles([d["volatility_pct"] for d in details
                                      if d["volatility_pct"] is not None]),
    }

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe": f"HS300 first {len(codes)}",
        "esg_coverage": sum(1 for d in details if d["esg_rating"]) / len(details),
        "risk_coverage": sum(1 for d in details if d["sharpe"] is not None) / len(details),
        "esg_distribution": esg_dist,
        "risk_quantiles": stats,
        "details": details,
        "raw_esg_answers": esg_raw,   # 原始帧留证，口径漂移可回溯
        "raw_risk_answers": risk_raw,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_factor_distribution.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nESG 覆盖 {payload['esg_coverage']:.0%} / 风险覆盖 {payload['risk_coverage']:.0%}")
    print(f"ESG 分布: {esg_dist}")
    for k, v in stats.items():
        print(f"{k}: {v}")
    print(f"落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
