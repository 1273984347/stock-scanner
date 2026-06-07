"""
Baseline 深度分析 — 324 笔回测摘要统计 + 风险评估
====================================================

输入：scripts/paper_vs_backtest.py 硬编码的 BACKTEST_BASELINE
      + cache/backtest_results/multi_*.json (100 点 nav 曲线)
输出：报告到 stdout + reports/baseline_analysis_{date}.json

执行：python reports/analyze_baseline.py
"""
import json
import math
import sys
from datetime import datetime
from pathlib import Path

# ST20: Windows GBK 安全
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ============== 1. Baseline 摘要 ==============

# 来自 paper_vs_backtest.py 硬编码 v3_hybrid baseline
BASELINE = {
    "version": "v3_hybrid",
    "window_days": 750,
    "stock_pool_size": 100,
    "total_trades": 324,
    "win_rate": 43.2,
    "avg_pnl": 6.14,
    "avg_win": 25.52,
    "avg_loss": -9.61,
    "profit_factor": 2.20,
    "max_drawdown": -22.90,
    "total_return_pct": 198.81,
    "survivorship_bias_adjusted_return_pct": 192.85,
}


def statistical_robustness(b: dict) -> dict:
    """统计稳健性分析 — 用 95% 置信区间评估结果是否够稳"""
    n = b["total_trades"]
    wr = b["win_rate"] / 100

    # 胜率 95% CI (Wald 区间)
    se_wr = math.sqrt(wr * (1 - wr) / n)
    wr_ci_low = max(0, (wr - 1.96 * se_wr)) * 100
    wr_ci_high = min(1, (wr + 1.96 * se_wr)) * 100

    # 期望收益 t 检验
    # E[pnl] = WR * avg_win + (1-WR) * avg_loss
    expected_pnl = wr * b["avg_win"] + (1 - wr) * b["avg_loss"]

    # 收益标准差估计: var = WR*(avg_win - E)^2 + (1-WR)*(avg_loss - E)^2
    var_pnl = wr * (b["avg_win"] - expected_pnl) ** 2 + (1 - wr) * (b["avg_loss"] - expected_pnl) ** 2
    std_pnl = math.sqrt(var_pnl)
    se_pnl = std_pnl / math.sqrt(n)
    t_stat = expected_pnl / se_pnl

    # 概率 p(avg_pnl > 0) — 正期望概率
    # 用正态近似: Z = E / SE
    z_score = expected_pnl / se_pnl
    # p-value (one-sided) — 用 z>0 的累计概率
    p_positive = 0.5 * (1 + math.erf(z_score / math.sqrt(2)))

    # Kelly 分数（风险调整建议仓位）
    # K = WR - (1-WR)/R, R = avg_win/abs(avg_loss)
    r_ratio = b["avg_win"] / abs(b["avg_loss"])
    kelly_full = wr - (1 - wr) / r_ratio
    kelly_half = kelly_full / 2  # 半 Kelly 实务推荐

    return {
        "样本量": n,
        "胜率_95CI_low": round(wr_ci_low, 2),
        "胜率_95CI_high": round(wr_ci_high, 2),
        "胜率_CI宽度": round(wr_ci_high - wr_ci_low, 2),
        "期望收益E": round(expected_pnl, 2),
        "标准差SE": round(se_pnl, 2),
        "t统计量": round(t_stat, 2),
        "正期望概率": round(p_positive * 100, 2),
        "盈亏比R": round(r_ratio, 2),
        "Kelly_全": round(kelly_full * 100, 2),
        "Kelly_半(推荐)": round(kelly_half * 100, 2),
    }


def annualized_metrics(b: dict) -> dict:
    """年化指标 — 750 天窗口"""
    years = b["window_days"] / 365.25
    annualized_return = ((1 + b["total_return_pct"] / 100) ** (1 / years) - 1) * 100
    annualized_survivorship = ((1 + b["survivorship_bias_adjusted_return_pct"] / 100) ** (1 / years) - 1) * 100

    # Sharpe 估计: 假设每日 PnL 分布 — 用 per-trade 数据
    # trades_per_year = 324 / years ≈ 158
    trades_per_year = b["total_trades"] / years
    # 年化波动率近似: per-trade std * sqrt(trades_per_year)
    # 已知 avg_win=25.52, avg_loss=-9.61, WR=0.432
    wr = b["win_rate"] / 100
    var_pnl = wr * (b["avg_win"] - b["avg_pnl"]) ** 2 + (1 - wr) * (b["avg_loss"] - b["avg_pnl"]) ** 2
    annual_vol = math.sqrt(var_pnl * trades_per_year)
    sharpe_estimate = annualized_return / annual_vol if annual_vol > 0 else 0

    return {
        "回测窗口_年": round(years, 2),
        "年化收益(原始)": round(annualized_return, 2),
        "年化收益(生存偏差调整)": round(annualized_survivorship, 2),
        "年化波动率_估计": round(annual_vol, 2),
        "Sharpe_估计": round(sharpe_estimate, 2),
        "年化交易数": round(trades_per_year, 0),
    }


def philosophy_compliance(b: dict) -> list:
    """对照量化哲学检查 (CLAUDE.md 项目级)"""
    checks = []

    # 1. 最大回撤 < 15% (个人哲学)
    if abs(b["max_drawdown"]) < 15:
        checks.append({"项": "最大回撤 < 15%", "结果": "PASS", "值": f"{b['max_drawdown']}%"})
    elif abs(b["max_drawdown"]) < 25:
        checks.append({"项": "最大回撤 < 15%", "结果": "WARN", "值": f"{b['max_drawdown']}% (超目标, 但 < 25% 可接受)"})
    else:
        checks.append({"项": "最大回撤 < 15%", "结果": "FAIL", "值": f"{b['max_drawdown']}% (严重超限)"})

    # 2. 胜率样本量足够 (n >= 100)
    if b["total_trades"] >= 100:
        checks.append({"项": "样本量 >= 100", "结果": "PASS", "值": f"{b['total_trades']} 笔"})
    else:
        checks.append({"项": "样本量 >= 100", "结果": "FAIL", "值": f"{b['total_trades']} 笔 (不足)"})

    # 3. 盈亏比 >= 1.5
    r_ratio = b["avg_win"] / abs(b["avg_loss"])
    if r_ratio >= 2.0:
        checks.append({"项": "盈亏比 >= 2.0", "结果": "PASS", "值": f"{r_ratio:.2f}"})
    elif r_ratio >= 1.5:
        checks.append({"项": "盈亏比 >= 1.5", "结果": "WARN", "值": f"{r_ratio:.2f}"})
    else:
        checks.append({"项": "盈亏比 >= 1.5", "结果": "FAIL", "值": f"{r_ratio:.2f}"})

    # 4. Profit Factor >= 1.5
    if b["profit_factor"] >= 2.0:
        checks.append({"项": "PF >= 2.0", "结果": "PASS", "值": f"{b['profit_factor']}"})
    elif b["profit_factor"] >= 1.5:
        checks.append({"项": "PF >= 1.5", "结果": "WARN", "值": f"{b['profit_factor']}"})
    else:
        checks.append({"项": "PF >= 1.5", "结果": "FAIL", "值": f"{b['profit_factor']}"})

    # 5. 生存偏差 < 10% (小调整 = 池子健康)
    bias_adj = abs(b["total_return_pct"] - b["survivorship_bias_adjusted_return_pct"]) / b["total_return_pct"]
    if bias_adj < 0.05:
        checks.append({"项": "生存偏差 < 5%", "结果": "PASS", "值": f"{bias_adj*100:.2f}%"})
    elif bias_adj < 0.10:
        checks.append({"项": "生存偏差 < 10%", "结果": "WARN", "值": f"{bias_adj*100:.2f}%"})
    else:
        checks.append({"项": "生存偏差 < 10%", "结果": "FAIL", "值": f"{bias_adj*100:.2f}%"})

    return checks


def analyze_nav_curve(nav: list) -> dict:
    """分析 nav 曲线（100 点）— 粗估最大回撤时点和恢复期"""
    if not nav or len(nav) < 10:
        return {"error": "nav 数据不足"}

    peak = nav[0]
    peak_idx = 0
    max_dd = 0
    max_dd_idx = 0
    recovery_idx = None

    in_drawdown = False

    for i, v in enumerate(nav):
        if v > peak:
            peak = v
            peak_idx = i
            in_drawdown = False
            recovery_idx = i
        dd = (v - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
            max_dd_idx = i
            in_drawdown = True
        elif in_drawdown and dd >= 0:
            # 已恢复
            in_drawdown = False

    return {
        "起点": nav[0],
        "终点": nav[-1],
        "总收益%": round((nav[-1] / nav[0] - 1) * 100, 2),
        "最大回撤%": round(max_dd, 2),
        "峰值idx": peak_idx,
        "谷底idx": max_dd_idx,
        "回撤持续期(bar)": max_dd_idx - (recovery_idx or 0),
        "样本数": len(nav),
    }


def load_old_nav() -> list:
    """加载老的 nav 曲线（仅参考，与 baseline 版本不同）"""
    candidates = list(Path("cache/backtest_results").glob("multi_*.json"))
    if not candidates:
        return []
    candidates.sort()
    try:
        with open(candidates[-1], encoding="utf-8", errors="replace") as f:
            d = json.load(f)
        return d.get("nav", [])
    except Exception:
        return []


def main():
    print("=" * 70)
    print(f"  Baseline 深度分析报告 - {BASELINE['version']}")
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    print("\n[1/4] 统计稳健性")
    stat = statistical_robustness(BASELINE)
    for k, v in stat.items():
        print(f"  {k:25s} {v}")

    print("\n[2/4] 年化指标")
    ann = annualized_metrics(BASELINE)
    for k, v in ann.items():
        print(f"  {k:25s} {v}")

    print("\n[3/4] 量化哲学合规检查")
    compliance = philosophy_compliance(BASELINE)
    for c in compliance:
        print(f"  [{c['结果']}] {c['项']:25s} {c['值']}")

    print("\n[4/4] 老 nav 曲线分析（参考，非 v3_hybrid）")
    nav = load_old_nav()
    if nav:
        nav_analysis = analyze_nav_curve(nav)
        for k, v in nav_analysis.items():
            print(f"  {k:25s} {v}")
    else:
        print("  [SKIP] 无 nav 数据")

    # ============== 总结 ==============
    print("\n" + "=" * 70)
    print("  [总结]")
    print("=" * 70)

    warnings = [c for c in compliance if c["结果"] in ("WARN", "FAIL")]
    if warnings:
        print(f"  [WARN] {len(warnings)} 项不达标:")
        for w in warnings:
            print(f"      - {w['项']}: {w['值']}")
    else:
        print("  [OK] 全部达标")

    p_pos = stat["正期望概率"]
    if p_pos > 95:
        print(f"  [OK] 期望收益稳健性极高 (p={p_pos:.1f}%)")
    elif p_pos > 80:
        print(f"  [WARN] 期望收益较稳健 (p={p_pos:.1f}%)，但需 paper trading 验证")
    else:
        print(f"  [FAIL] 期望收益统计显著性不足 (p={p_pos:.1f}%)")

    ann_ret = ann["年化收益(生存偏差调整)"]
    if ann_ret > 50:
        print(f"  [WARN] 年化收益 {ann_ret:.1f}% 偏高，警惕过拟合")
    elif ann_ret > 20:
        print(f"  [OK] 年化收益 {ann_ret:.1f}% 合理范围")
    else:
        print(f"  [WARN] 年化收益 {ann_ret:.1f}% 偏低")

    # ============== 保存 JSON 报告 ==============
    report = {
        "generated_at": datetime.now().isoformat(),
        "baseline": BASELINE,
        "statistical_robustness": stat,
        "annualized_metrics": ann,
        "philosophy_compliance": compliance,
        "nav_curve_analysis": analyze_nav_curve(nav) if nav else None,
        "warnings": warnings,
    }

    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    out_path = report_dir / f"baseline_analysis_{datetime.now().strftime('%Y%m%d')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  报告已保存: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
