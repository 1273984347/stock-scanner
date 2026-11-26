#!/usr/bin/env python3
"""严格前瞻分组验证：T0 指标分组 -> T0 之后收益（样本外）

状态（2026-11-27）：**本条线已关闭**（ADR-0037 决策4）。本脚本已完成使命——两个等长不重叠窗口
方向翻转（+40.0pp / −35.9pp），结论为不采纳加权。保留用于复跑取证与未来重启时的对照，
不得据此再加第三时点（挑选窗口 = 数据窥探）。

与 ifind_risk_group_backtest.py 的区别（关键）：
- 旧脚本：iFinD 指标截止日=最新，K 线为同期 => 同期一致性，非前瞻。
- 本脚本：指标取历史时点 T0（20260526），收益取 T0 之后（20260527~今）
  => 分组信息与收益窗口完全时间隔离，是真样本外前瞻。

流程：
1. 读 ifind_historical_sharpe.py 落盘的 T0 Sharpe（只取 valid=True）
2. 按 Sharpe 三分位分组（高/中/低）
3. 对每只股票取 K 线，切出 [T0, T0 + horizon_months] 的部分，
   算区间收益/回撤/Sharpe
4. 比较组间差异，检验单调性；并给出 Spearman 秩相关与相对全样本的超额收益

为什么必须锚定日历窗口（而非"取满 N 根 K 线"）：
- 不同 T0 可用 K 线长度不同（20251126 可覆盖 12 个月、20260526 只有 6 个月），
  不截断会让两个时点不可比；而按"固定 126 根"截断时，停牌股的窗口会顺延到 T0+7 个月，
  与下一个窗口的起点重叠（本次实测最长重叠 4 周），破坏"两窗口独立"的前提。
- 锚定日历（T0 + 6 个月）可保证两窗口**精确首尾相接、零重叠**，且同一窗口内所有股票
  覆盖同一经济区间，停牌只减少样本点数、不拉长区间。

输出：cache/ifind/*_strict_forward_{T0}.json
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


def _load_sharpe(t0: str) -> tuple[list[dict], str]:
    files = sorted(CACHE.glob(f"*_historical_sharpe_{t0}.json"))
    if not files:
        raise RuntimeError(f"无 T0={t0} 历史 Sharpe 文件，先跑 ifind_historical_sharpe.py")
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    valid = [r for r in data["records"] if r["valid"] and r["sharpe_t0"] is not None]
    return valid, files[-1].name


def _add_months(d: datetime, months: int) -> datetime:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    # 月末对齐：1/31 + 1 月 -> 2/28（或 29）
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return d.replace(year=y, month=m, day=day)


def _forward_metrics(code: str, t0: str, horizon_months: int = 6) -> dict | None:
    """取 [T0, T0+horizon_months] 的 K 线，算前瞻收益（严格只用该窗口内数据）

    日历锚定而非"取满 N 根"：保证不同 T0 的窗口等长、零重叠（见模块文档串）。
    """
    from lib.data.data_layer import get_stock_hist_multi_source

    try:
        df = get_stock_hist_multi_source(code, days=400, source="auto", prefer_adjusted=True)
    except Exception:
        return None
    if df is None or len(df) < 30:
        return None
    t0_dt = datetime.strptime(t0, "%Y%m%d")
    end_dt = _add_months(t0_dt, horizon_months)
    t0_norm = t0_dt.strftime("%Y-%m-%d")
    end_norm = end_dt.strftime("%Y-%m-%d")
    dates = df["date"].astype(str).str[:10]
    fwd = df[(dates >= t0_norm) & (dates <= end_norm)].copy()
    if len(fwd) < 20:
        return None
    fwd = fwd.sort_values("date")
    close = fwd["close"].astype(float)
    if (close <= 0).any():
        return None
    rets = close.pct_change().dropna()
    total_return = float(close.iloc[-1] / close.iloc[0] - 1)
    peak = close.cummax()
    max_dd = float((close / peak - 1).min() * -100)
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if len(rets) > 5 and rets.std() > 0 else 0.0
    return {
        "forward_return_pct": round(total_return * 100, 3),
        "forward_max_dd_pct": round(max_dd, 3),
        "forward_sharpe": round(sharpe, 4),
        "n_bars": len(fwd),
        "start": str(fwd["date"].iloc[0])[:10],
        "end": str(fwd["date"].iloc[-1])[:10],
        "nominal_end": end_norm,
        "short_window": bool(str(fwd["date"].iloc[-1])[:10] < end_norm),
    }


def _rank_ic(x: list[float], y: list[float]) -> float | None:
    """Spearman 秩相关（不依赖 scipy）：分组收益若由市场 beta 主导，秩相关会显著偏离 0"""
    if len(x) < 10 or len(x) != len(y):
        return None

    def ranks(v: list[float]) -> np.ndarray:
        order = np.argsort(np.asarray(v, dtype=float), kind="mergesort")
        r = np.empty(len(v), dtype=float)
        r[order] = np.arange(len(v), dtype=float)
        return r

    rx, ry = ranks(x), ranks(y)
    rx -= rx.mean()
    ry -= ry.mean()
    denom = float(np.sqrt((rx ** 2).sum() * (ry ** 2).sum()))
    return None if denom == 0 else float((rx * ry).sum() / denom)


def _group_stats(rows: list[dict], sample_mean: float | None = None) -> dict:
    out = {}
    for label in ("high_sharpe", "mid_sharpe", "low_sharpe"):
        grp = [r for r in rows if r["group"] == label and r.get("forward")]
        if not grp:
            continue
        rets = [r["forward"]["forward_return_pct"] for r in grp]
        dds = [r["forward"]["forward_max_dd_pct"] for r in grp]
        shps = [r["forward"]["forward_sharpe"] for r in grp]
        entry = {
            "n": len(grp),
            "t0_sharpe_mean": round(float(np.mean([r["sharpe_t0"] for r in grp])), 4),
            "forward_return_mean_pct": round(float(np.mean(rets)), 3),
            "forward_return_median_pct": round(float(np.median(rets)), 3),
            "forward_return_win_rate": round(float(np.mean([r > 0 for r in rets])), 3),
            "forward_mdd_mean_pct": round(float(np.mean(dds)), 3),
            "forward_sharpe_mean": round(float(np.mean(shps)), 4),
        }
        if sample_mean is not None:
            # 超额 = 组均值 - 全样本等权均值：剔除市场 beta，只看因子本身的横截面信息
            entry["forward_excess_mean_pct"] = round(float(np.mean(rets)) - sample_mean, 3)
        out[label] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--t0", default="20260526", help="指标取数时点 YYYYMMDD")
    ap.add_argument("--horizon-months", type=int, default=6,
                    help="前瞻日历月数，默认 6；跨 T0 必须一致（保证零重叠）")
    args = ap.parse_args()

    records, src = _load_sharpe(args.t0)
    print(f"T0={args.t0} / 有效指标样本 {len(records)} 只（来源 {src}）")
    print(f"前瞻窗口 [{args.t0}, +{args.horizon_months} 月]（日历锚定，跨 T0 等长）")

    values = [r["sharpe_t0"] for r in records]
    t1, t2 = np.percentile(values, [33.33, 66.67])
    print(f"三分位阈值: 低<={t1:.4f} / 中<={t2:.4f} / 高>{t2:.4f}")

    rows = []
    for i, r in enumerate(records):
        v = r["sharpe_t0"]
        group = "high_sharpe" if v >= t2 else ("mid_sharpe" if v >= t1 else "low_sharpe")
        fwd = _forward_metrics(r["code"], args.t0, args.horizon_months)
        rows.append({"code": r["code"], "sharpe_t0": v, "group": group, "forward": fwd})
        if (i + 1) % 25 == 0:
            print(f"  前瞻K线进度 {i + 1}/{len(records)}", flush=True)

    ok = [r for r in rows if r.get("forward")]
    sample_mean = float(np.mean([r["forward"]["forward_return_pct"] for r in ok])) if ok else None
    stats = _group_stats(rows, sample_mean)
    rank_ic = _rank_ic([r["sharpe_t0"] for r in ok],
                       [r["forward"]["forward_return_pct"] for r in ok])

    print("\n=== 严格前瞻结果（T0 分组 -> T0 后等长窗口收益）===")
    print(f"{'组':<14} {'n':>4} {'T0 Sharpe':>11} {'前瞻收益均值%':>14} {'超额%':>9} {'中位数%':>10} {'胜率':>7} {'前瞻回撤%':>11} {'前瞻Sharpe':>11}")
    for label in ("high_sharpe", "mid_sharpe", "low_sharpe"):
        s = stats.get(label)
        if not s:
            continue
        print(f"{label:<14} {s['n']:>4} {s['t0_sharpe_mean']:>11.4f} "
              f"{s['forward_return_mean_pct']:>14.2f} {s.get('forward_excess_mean_pct', 0):>9.2f} "
              f"{s['forward_return_median_pct']:>10.2f} "
              f"{s['forward_return_win_rate']:>7.2f} {s['forward_mdd_mean_pct']:>11.2f} "
              f"{s['forward_sharpe_mean']:>11.4f}")

    hi = stats.get("high_sharpe", {}).get("forward_return_mean_pct")
    lo = stats.get("low_sharpe", {}).get("forward_return_mean_pct")
    hi_sh = stats.get("high_sharpe", {}).get("forward_sharpe_mean")
    lo_sh = stats.get("low_sharpe", {}).get("forward_sharpe_mean")
    monotonic = (hi is not None and lo is not None and hi > lo)
    print(f"\n全样本等权前瞻收益均值: {sample_mean:.2f}%")
    print(f"高 Sharpe 组前瞻收益 > 低 Sharpe 组: {'成立' if monotonic else '不成立'} "
          f"({hi}% vs {lo}%)")
    print(f"Spearman(T0 Sharpe, 前瞻收益) = {rank_ic:.4f}" if rank_ic is not None else "rank IC 不足")
    if hi_sh is not None and lo_sh is not None:
        print(f"前瞻 Sharpe: 高组 {hi_sh} vs 低组 {lo_sh}")

    out = CACHE / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_strict_forward_{args.t0}.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "t0": args.t0,
        "source_sharpe_file": src,
        "valid_n": len(records),
        "horizon_months": args.horizon_months,
        "window": {"start": datetime.strptime(args.t0, "%Y%m%d").strftime("%Y-%m-%d"),
                   "end": _add_months(datetime.strptime(args.t0, "%Y%m%d"),
                                      args.horizon_months).strftime("%Y-%m-%d")},
        "n_short_window": sum(1 for r in ok if r["forward"].get("short_window")),
        "tercile": {"t1": round(float(t1), 4), "t2": round(float(t2), 4)},
        "sample_mean_forward_pct": round(sample_mean, 3) if sample_mean is not None else None,
        "rank_ic": round(rank_ic, 4) if rank_ic is not None else None,
        "monotonic": monotonic,
        "group_stats": stats,
        "rows": rows,
        "design": ("T0 指标分组 + [T0, T0+horizon_months] 日历锚定窗口 K 线收益，"
                   "跨 T0 等长且零重叠，时间严格隔离；超额=组均值-全样本等权均值"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
