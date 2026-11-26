#!/usr/bin/env python3
"""iFinD 风险指标三分位分组 —— 前瞻收益验证（先导 50 只）

状态（2026-11-27）：**本条线已关闭**（ADR-0037 决策4，严格前瞻证伪）。本脚本仅作复跑取证，
不再作为活跃研究线；其"同期一致性"结果不得作为收益端证据引用。

目的：回答 ADR-0037 迁移步骤 2 ——"低风险组是否有超额收益"。

严格的无前视设计（关键）：
- 分组指标来自 iFinD 当前截面（截至 2026-11-26 的 52 周窗口），**这是历史区间的汇总值**；
- 因此前瞻收益必须从**分组日之前**的 K 线窗口计算，而非之后：
  用 K 线 [T-252, T] 自算该窗口的收益，与 iFinD 分组指标配对。
  ⚠️ 这仍然是"同期相关性"而非真正的前瞻预测——本脚本如实标注，
     不宣称预测力，只验证"分组指标与已实现风险收益是否一致"（方向一致性）。
- 真正的前瞻需要分组日之后再取收益，但 iFinD 指标截止日=最新，无未来窗口可用；
  若要有真前瞻，需在未来日期重新取一次指标后滚动验证（写入 ADR 限制）。

两种验证：
A. 同期一致性：iFinD 分组 vs K 线自算同期（52周）收益/回撤，检查分组是否与真实历史风险对齐；
B. 组合回测：每组走 backtest.unified simple 模式，作为交叉验证（注意信号稀疏时 0 交易属正常）。

输出：cache/ifind/*_risk_group_backtest.json，含分组明细与组间统计。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "ifind"
sys.path.insert(0, str(ROOT))


def _load_distribution(size: int, use_filled: bool = False) -> list[dict]:
    pattern = "*_factor_distribution_filled.json" if use_filled else "*_factor_distribution.json"
    files = sorted(CACHE.glob(pattern))
    if not files:
        raise RuntimeError(f"无分布取证文件（pattern={pattern}）")
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    details = data["details"][:size]
    if use_filled:
        # 原值为 None 时用单指标重查值补上；保留原字段与来源标记，可追溯
        for row in details:
            if row.get("volatility_pct") is None and row.get("volatility_pct_filled") is not None:
                row["volatility_pct"] = row["volatility_pct_filled"]
    return details


def _kline_metrics(code: str, window: int = 252) -> dict | None:
    """用 K 线自算同期风险收益指标（独立于 iFinD，避免同源循环论证）"""
    from lib.data.data_layer import get_stock_hist_multi_source

    try:
        df = get_stock_hist_multi_source(code, days=window, source="auto", prefer_adjusted=True)
    except Exception:
        return None
    if df is None or len(df) < 60:
        return None
    close = df["close"].astype(float)
    if (close <= 0).any():
        return None
    rets = close.pct_change().dropna()
    if len(rets) < 30:
        return None
    total_return = float(close.iloc[-1] / close.iloc[0] - 1)
    peak = close.cummax()
    max_dd = float((close / peak - 1).min() * -100)
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    return {
        "total_return_pct": round(total_return * 100, 3),
        "max_drawdown_pct": round(max_dd, 3),
        "sharpe_annualized": round(sharpe, 4),
        "n_bars": len(df),
        "window_start": str(df["date"].iloc[0])[:10],
        "window_end": str(df["date"].iloc[-1])[:10],
    }


def _tercile(values: list[float]) -> tuple[float, float]:
    """返回 1/3、2/3 分位阈值"""
    arr = np.array([v for v in values if v is not None], dtype=float)
    return float(np.percentile(arr, 33.33)), float(np.percentile(arr, 66.67))


def _group_label(value: float | None, t1: float, t2: float, low_is_good: bool = True) -> str:
    if value is None:
        return "unknown"
    if low_is_good:
        if value <= t1:
            return "low_risk"
        if value <= t2:
            return "mid_risk"
        return "high_risk"
    if value >= t2:
        return "low_risk"
    if value >= t1:
        return "mid_risk"
    return "high_risk"


def _group_stats(rows: list[dict]) -> dict:
    out = {}
    for label in ("low_risk", "mid_risk", "high_risk", "unknown"):
        grp = [r for r in rows if r["group"] == label]
        if not grp:
            continue
        valid = [r for r in grp if r.get("kline")]
        out[label] = {
            "n": len(grp),
            "n_with_kline": len(valid),
            "ifind_mdd_mean": round(float(np.mean([r["ifind_mdd"] for r in grp if r["ifind_mdd"] is not None])), 2)
            if any(r["ifind_mdd"] is not None for r in grp) else None,
            "kline_return_mean_pct": round(float(np.mean([r["kline"]["total_return_pct"] for r in valid])), 2) if valid else None,
            "kline_mdd_mean_pct": round(float(np.mean([r["kline"]["max_drawdown_pct"] for r in valid])), 2) if valid else None,
            "kline_sharpe_mean": round(float(np.mean([r["kline"]["sharpe_annualized"] for r in valid])), 4) if valid else None,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", type=int, default=50, help="先导样本数")
    ap.add_argument("--metric", default="mdd", choices=["mdd", "vol", "sharpe"], help="分组主轴")
    ap.add_argument("--use-filled", action="store_true", help="使用单指标重查补全的波动率（10只）")
    args = ap.parse_args()

    details = _load_distribution(args.size, use_filled=args.use_filled)
    if not details:
        raise RuntimeError("分布数据为空")

    key = {"mdd": "max_drawdown_pct", "vol": "volatility_pct", "sharpe": "sharpe"}[args.metric]
    values = [d.get(key) for d in details]
    t1, t2 = _tercile(values)
    low_is_good = args.metric in ("mdd", "vol")
    print(f"分组主轴: {args.metric} / 阈值 t1={t1:.4f} t2={t2:.4f} / low_is_good={low_is_good}")
    print(f"样本: {len(details)} 只")

    rows = []
    for i, d in enumerate(details):
        code = d["code"]
        value = d.get(key)
        group = _group_label(value, t1, t2, low_is_good)
        kline = _kline_metrics(code)
        rows.append({
            "code": code,
            "ifind_mdd": d.get("max_drawdown_pct"),
            "ifind_vol": d.get("volatility_pct"),
            "ifind_sharpe": d.get("sharpe"),
            "esg_rating": d.get("esg_rating"),
            "group_metric": value,
            "group": group,
            "kline": kline,
        })
        if (i + 1) % 10 == 0:
            print(f"  K线进度 {i + 1}/{len(details)}", flush=True)

    stats = _group_stats(rows)
    print("\n=== 分组结果 ===")
    header = f"{'组':<12} {'n':>4} {'有K线':>6} {'iFinD回撤均值':>14} {'K线收益均值%':>14} {'K线回撤均值%':>14} {'K线Sharpe':>10}"
    print(header)
    for label in ("low_risk", "mid_risk", "high_risk", "unknown"):
        s = stats.get(label)
        if not s:
            continue
        print(f"{label:<12} {s['n']:>4} {s['n_with_kline']:>6} "
              f"{str(s['ifind_mdd_mean']):>14} {str(s['kline_return_mean_pct']):>14} "
              f"{str(s['kline_mdd_mean_pct']):>14} {str(s['kline_sharpe_mean']):>10}")

    direction_ok = None
    if args.metric in ("mdd", "vol") and "low_risk" in stats and "high_risk" in stats:
        lr = stats["low_risk"].get("kline_mdd_mean_pct")
        hr = stats["high_risk"].get("kline_mdd_mean_pct")
        if lr is not None and hr is not None:
            direction_ok = lr < hr
            print(f"\n方向一致性（低风险组K线回撤 < 高风险组）: {'成立' if direction_ok else '不成立'} "
                  f"({lr}% vs {hr}%)")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample_size": len(details),
        "group_metric": args.metric,
        "tercile_thresholds": {"t1": round(t1, 4), "t2": round(t2, 4)},
        "low_is_good": low_is_good,
        "direction_consistency": direction_ok,
        "group_stats": stats,
        "rows": rows,
        "caveat": "iFinD 风险指标截止日=最新，K线窗口同期；本验证为同期一致性，非真前瞻预测",
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_risk_group_backtest_{args.size}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
