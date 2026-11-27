#!/usr/bin/env python3
"""风险指标 vs ESG 等级区分力分析（取证脚本）

输入：ifind_factor_distribution.py 落盘的 *_factor_distribution.json（取最新）
输出：区分力判据——风险指标的离散度、分位跨度、相互相关性，
      并与 ESG 分组对比，判断"风险指标本身比 ESG 等级更有区分力"是否成立。

判据设计（避免空判据）：
1. 变异系数 CV = std/mean：风险指标 CV 应显著 > ESG 等级序数的 CV
2. 分位跨度：p90/p10 比值应 > 1.5 才算有区分空间
3. 相关性：Sharpe 与回撤/波动率的相关系数（若高度相关则是同一信息重复）
4. ESG 分组的单调性检验：等级序数与风险指标的 Spearman 相关性，
   绝对值 < 0.3 视为"ESG 无区分力"（本次取证的核心结论）

⚠️ 本条线（iFinD 风险因子纳入评分）已于 2026-11-27 关闭（ADR-0037 §决策4）。
   本脚本仅作**复跑取证 / 重启资产**，不参与生产路径。关键快照固化于
   docs/adr/0037-evidence/factor_signal_analysis.json。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "ifind"


def _load_latest() -> dict:
    files = sorted(CACHE.glob("*_factor_distribution.json"))
    if not files:
        raise RuntimeError("无分布取证文件")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman 秩相关（无 scipy 依赖）"""
    if len(x) < 3 or len(x) != len(y):
        return 0.0
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    if rx.std() == 0 or ry.std() == 0:
        return 0.0
    return float(np.corrcoef(rx, ry)[0, 1])


def main() -> int:
    data = _load_latest()
    details = data["details"]
    print(f"样本: {data['universe']} / n={len(details)}")

    esg_order = {"AAA": 6, "AA": 5, "A": 4, "BBB": 3, "BB": 2, "B": 1, "CCC": 1, "C": 0}
    metrics = {
        "sharpe": [d["sharpe"] for d in details],
        "max_drawdown_pct": [d["max_drawdown_pct"] for d in details],
        "volatility_pct": [d["volatility_pct"] for d in details],
        "esg_rank": [esg_order.get(d["esg_rating"], np.nan) for d in details],
    }

    print("\n=== 1. 离散度（CV = std/mean）与分位跨度 ===")
    stats = {}
    for name, values in metrics.items():
        arr = np.array([v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))], dtype=float)
        if len(arr) < 3:
            continue
        cv = arr.std() / abs(arr.mean()) if arr.mean() != 0 else float("inf")
        p10, p90 = np.percentile(arr, [10, 90])
        span = p90 / p10 if p10 > 0 else (float("inf") if p90 > 0 else 0)
        stats[name] = {"n": len(arr), "mean": round(float(arr.mean()), 4),
                       "std": round(float(arr.std()), 4), "cv": round(float(cv), 3),
                       "p10": round(float(p10), 4), "p90": round(float(p90), 4),
                       "span_p90_p10": round(float(span), 3)}
        print(f"  {name:20s} n={len(arr):3d} CV={cv:6.3f} p10={p10:9.4f} p90={p90:9.4f} span={span:6.3f}")

    print("\n=== 2. 风险指标相互相关性（Pearson / Spearman） ===")
    pairs = [("sharpe", "max_drawdown_pct"), ("sharpe", "volatility_pct"),
             ("max_drawdown_pct", "volatility_pct")]
    for a, b in pairs:
        xa = [(d[a], d[b]) for d in details if d[a] is not None and d[b] is not None]
        if len(xa) < 5:
            continue
        x = [v[0] for v in xa]; y = [v[1] for v in xa]
        pear = float(np.corrcoef(x, y)[0, 1]) if len(x) > 2 else 0.0
        spear = _spearman(x, y)
        print(f"  {a:18s} vs {b:18s} pearson={pear:+.3f} spearman={spear:+.3f} n={len(x)}")

    print("\n=== 3. ESG 等级 vs 风险指标单调性（Spearman） ===")
    for metric in ("sharpe", "max_drawdown_pct", "volatility_pct"):
        pairs2 = [(esg_order.get(d["esg_rating"], np.nan), d[metric]) for d in details
                  if d["esg_rating"] and d[metric] is not None]
        pairs2 = [(a, b) for a, b in pairs2 if not np.isnan(a)]
        if len(pairs2) < 5:
            continue
        rho = _spearman([p[0] for p in pairs2], [p[1] for p in pairs2])
        verdict = "无单调性" if abs(rho) < 0.3 else ("弱单调" if abs(rho) < 0.5 else "显著单调")
        print(f"  ESG vs {metric:18s} rho={rho:+.3f} -> {verdict} (n={len(pairs2)})")

    print("\n=== 结论判据 ===")
    risk_cvs = [stats[k]["cv"] for k in ("sharpe", "max_drawdown_pct", "volatility_pct") if k in stats]
    esg_cv = stats.get("esg_rank", {}).get("cv", 0)
    print(f"  风险指标平均 CV = {np.mean(risk_cvs):.3f} vs ESG 等级 CV = {esg_cv:.3f}")
    print(f"  风险指标 CV 更高: {'是' if np.mean(risk_cvs) > esg_cv else '否'}")

    out = CACHE / "factor_signal_analysis.json"
    out.write_text(json.dumps({
        "source_sample": data["universe"],
        "n": len(details),
        "stats": stats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
