#!/usr/bin/env python3
"""诊断：Sharpe 分组的"前瞻方向翻转"是否只是 beta/波动暴露

状态（2026-11-27）：**本条线已关闭**（ADR-0037 决策4）。本脚本仅作复跑取证：
窗口 2 高组 beta 1.41 vs 低组 0.60、beta 调整后跨度仍 −23pp，证明反转不只是 beta 混淆。

背景（2026-11-26）：
- 同期分组验证（ifind_risk_group_backtest.py）显示 Sharpe 主轴完全单调；
- 严格前瞻两个等长窗口结论相反：
  T0=2025-11-26 -> 高 Sharpe 组 +32.1% vs 低组 -6.0%（IC +0.357）
  T0=2026-05-26 -> 高 Sharpe 组 -36.6% vs 低组 -0.7%（IC -0.636）
- 若高 Sharpe 组只是"高 beta"，则反转可由市场方向解释，而非因子失效。

本脚本用同一批 K 线（T0 之前 126 根）算：
1. 事前年化波动率（pre_vol）
2. 相对等权同业组合的 beta（pre_beta）——市场代理 = 样本内等权日收益，非 HS300 指数
3. beta 调整后的前瞻超额：fwd_return - pre_beta * 样本等权前瞻收益
4. 组间均值 + 相关系数（sharpe_t0 vs pre_beta / pre_vol）

判读：
- 若高 Sharpe 组 pre_beta 显著更高 -> 反转主要由 beta 解释，静态加分等于加杠杆；
- 若 beta 调整后两窗口跨度仍反向 -> 因子本身不稳定，与 beta 无关。

输出：cache/ifind/*_sharpe_beta_diag.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "ifind"
sys.path.insert(0, str(ROOT))

PRE_BARS = 126


def _pre_returns(code: str, t0: str) -> pd.Series | None:
    from lib.data.data_layer import get_stock_hist_multi_source

    try:
        df = get_stock_hist_multi_source(code, days=800, source="auto", prefer_adjusted=True)
    except Exception:
        return None
    if df is None or len(df) < PRE_BARS + 10:
        return None
    t0_norm = f"{t0[:4]}-{t0[4:6]}-{t0[6:]}"
    d = df[df["date"].astype(str) <= t0_norm].copy()
    if len(d) < PRE_BARS:
        return None
    d = d.sort_values("date").iloc[-PRE_BARS:]
    s = pd.Series(d["close"].astype(float).values,
                  index=d["date"].astype(str).str[:10].values)
    if (s <= 0).any():
        return None
    return s.pct_change().dropna()


def _load_forward(path: str) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = CACHE / p
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forward", nargs="+", required=True,
                    help="strict_forward 产物文件名或绝对路径（可多个）")
    args = ap.parse_args()

    out_windows = []
    for fp in args.forward:
        payload = _load_forward(fp)
        t0 = payload["t0"]
        rets = {}
        for r in payload["rows"]:
            s = _pre_returns(r["code"], t0)
            if s is not None and len(s) > 60:
                rets[r["code"]] = s
        if len(rets) < 30:
            # 数据源历史仅 ~254 根（约 1 年），T0 早于"今天-126 交易日"时无法算事前 beta。
            # 如实跳过并记录原因，不用缩短窗口强行凑样本（会让窗口间不可比）。
            print(f"T0={t0} 跳过：事前收益样本 {len(rets)} < 30（历史深度不足 {PRE_BARS} 根）")
            out_windows.append({
                "t0": t0,
                "src": Path(fp).name,
                "skipped": True,
                "skip_reason": f"K 线源历史仅 ~254 根，T0 之前不足 {PRE_BARS} 根",
                "n_pre_available": len(rets),
            })
            continue

        panel = pd.DataFrame(rets).sort_index()
        # 等权同业组合：每日至少 30 只才算有效交易日
        mkt = panel.mean(axis=1, skipna=True)
        mkt[panel.notna().sum(axis=1) < 30] = np.nan
        mkt = mkt.dropna()
        var_m = float(mkt.var())

        rows = []
        for r in payload["rows"]:
            code = r["code"]
            if code not in panel.columns or not r.get("forward"):
                continue
            s = panel[code].dropna()
            joined = pd.concat([s, mkt], axis=1, join="inner").dropna()
            if len(joined) < 60:
                continue
            ri, rm = joined.iloc[:, 0].values, joined.iloc[:, 1].values
            beta = float(np.cov(ri, rm)[0, 1] / var_m) if var_m > 0 else None
            pre_vol = float(np.std(ri, ddof=1) * np.sqrt(252) * 100)
            rows.append({
                "code": code,
                "group": r["group"],
                "sharpe_t0": r["sharpe_t0"],
                "pre_beta": round(beta, 4) if beta is not None else None,
                "pre_vol_pct": round(pre_vol, 3),
                "fwd_return_pct": r["forward"]["forward_return_pct"],
                "n_pre": len(joined),
            })

        sample_mean = float(np.mean([x["fwd_return_pct"] for x in rows]))
        groups = {}
        for label in ("high_sharpe", "mid_sharpe", "low_sharpe"):
            grp = [x for x in rows if x["group"] == label]
            if not grp:
                continue
            betas = [x["pre_beta"] for x in grp if x["pre_beta"] is not None]
            vols = [x["pre_vol_pct"] for x in grp]
            raw = [x["fwd_return_pct"] for x in grp]
            adj = [x["fwd_return_pct"] - x["pre_beta"] * sample_mean
                   for x in grp if x["pre_beta"] is not None]
            groups[label] = {
                "n": len(grp),
                "pre_beta_mean": round(float(np.mean(betas)), 4) if betas else None,
                "pre_vol_mean_pct": round(float(np.mean(vols)), 3),
                "fwd_return_mean_pct": round(float(np.mean(raw)), 3),
                "beta_adj_return_mean_pct": round(float(np.mean(adj)), 3) if adj else None,
            }

        def _corr(a, b):
            pairs = [(x[a], x[b]) for x in rows if x[a] is not None and x[b] is not None]
            if len(pairs) < 20:
                return None
            xs, ys = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
            return round(float(np.corrcoef(xs, ys)[0, 1]), 4)

        hi, lo = groups.get("high_sharpe", {}), groups.get("low_sharpe", {})
        out_windows.append({
            "t0": t0,
            "src": Path(fp).name,
            "n": len(rows),
            "sample_mean_forward_pct": round(sample_mean, 3),
            "corr_sharpe_beta": _corr("sharpe_t0", "pre_beta"),
            "corr_sharpe_vol": _corr("sharpe_t0", "pre_vol_pct"),
            "groups": groups,
            "spread_raw_pp": round(hi["fwd_return_mean_pct"] - lo["fwd_return_mean_pct"], 2)
            if hi and lo else None,
            "spread_beta_adj_pp": round(hi["beta_adj_return_mean_pct"] - lo["beta_adj_return_mean_pct"], 2)
            if hi.get("beta_adj_return_mean_pct") is not None and lo.get("beta_adj_return_mean_pct") is not None else None,
            "rows": rows,
        })

    print(f"{'T0':<10} {'n':>4} {'高组beta':>9} {'低组beta':>9} {'corr(Sharpe,beta)':>19} "
          f"{'原始跨度pp':>10} {'beta调整跨度pp':>15}")
    for w in out_windows:
        if w.get("skipped"):
            print(f"{w['t0']:<10} 跳过（{w['skip_reason']}）")
            continue
        hi, lo = w["groups"].get("high_sharpe", {}), w["groups"].get("low_sharpe", {})
        print(f"{w['t0']:<10} {w['n']:>4} {hi.get('pre_beta_mean'):>9} {lo.get('pre_beta_mean'):>9} "
              f"{str(w['corr_sharpe_beta']):>19} {str(w['spread_raw_pp']):>10} {str(w['spread_beta_adj_pp']):>15}")

    out = CACHE / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_sharpe_beta_diag.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pre_bars": PRE_BARS,
        "market_proxy": "样本内等权同业日收益（非 HS300 指数）",
        "windows": out_windows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
