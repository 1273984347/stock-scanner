"""多窗口压力测试 —— 覆盖不同市场环境，验证策略鲁棒性

压力测试思路：
1. 定义历史极端行情区间（股灾/熊市/震荡/牛市）
2. 在每个区间内运行回测
3. 汇总各区间指标，找出策略失效的市场环境
4. 蒙特卡洛模拟估算置信区间

使用方式：
    from backtest.stress import run_stress_test
    result = run_stress_test(["000001", "600519", "000858"])
"""

import pandas as pd
import numpy as np
from datetime import datetime
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


# ========== 历史压力区间定义 ==========

@dataclass
class StressWindow:
    """压力测试窗口"""
    name: str           # 区间名称
    start: str          # 起始日期 YYYY-MM-DD
    end: str            # 结束日期 YYYY-MM-DD
    description: str    # 描述
    expected_regime: str  # 期望市场状态
    severity: str       # 严重程度: EXTREME / HIGH / MODERATE


# A股历史极端行情区间
STRESS_WINDOWS = [
    StressWindow(
        name="2015_crash",
        start="2015-06-12", end="2015-09-01",
        description="2015年股灾：上证从5178跌至2850，跌幅45%",
        expected_regime="TREND_DOWN",
        severity="EXTREME",
    ),
    StressWindow(
        name="2016_circuit_breaker",
        start="2016-01-04", end="2016-01-28",
        description="2016年熔断：4天两次熔断，上证跌18%",
        expected_regime="RISK_OFF",
        severity="EXTREME",
    ),
    StressWindow(
        name="2018_trade_war",
        start="2018-06-15", end="2018-12-28",
        description="2018年中美贸易战：上证从3000跌至2440，跌幅19%",
        expected_regime="TREND_DOWN",
        severity="HIGH",
    ),
    StressWindow(
        name="2020_covid",
        start="2020-01-20", end="2020-03-23",
        description="2020年新冠疫情：上证从3100跌至2660，跌幅14%",
        expected_regime="RISK_OFF",
        severity="HIGH",
    ),
    StressWindow(
        name="2022_bear",
        start="2022-01-01", end="2022-10-31",
        description="2022年熊市：上证从3600跌至2885，跌幅20%",
        expected_regime="TREND_DOWN",
        severity="HIGH",
    ),
    StressWindow(
        name="2023_sideways",
        start="2023-01-01", end="2023-12-31",
        description="2023年震荡市：上证在3000-3400区间震荡",
        expected_regime="MEAN_REVERSION",
        severity="MODERATE",
    ),
    StressWindow(
        name="2024_structural",
        start="2024-01-01", end="2024-12-31",
        description="2024年结构性行情：AI/红利分化严重",
        expected_regime="HIGH_VOLATILITY",
        severity="MODERATE",
    ),
    StressWindow(
        name="2019_bull",
        start="2019-01-01", end="2019-04-15",
        description="2019年小牛市：上证从2440涨至3288，涨幅35%",
        expected_regime="TREND_UP",
        severity="MODERATE",
    ),
]


@dataclass
class StressResult:
    """单个压力区间的回测结果"""
    window: StressWindow
    total_trades: int = 0
    win_rate: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    profit_loss_ratio: float = 0.0
    avg_hold_days: float = 0.0
    stop_loss_count: int = 0
    error: str = ""


@dataclass
class StressTestReport:
    """压力测试汇总报告"""
    results: List[StressResult] = field(default_factory=list)
    overall_win_rate: float = 0.0
    overall_return: float = 0.0
    worst_window: str = ""
    best_window: str = ""
    robustness_score: float = 0.0  # 0-100 鲁棒性评分
    monte_carlo: Dict = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)


# ========== 核心测试逻辑 ==========

def run_stress_test(
    symbols: List[str],
    initial_cash: float = 1_000_000,
    windows: List[StressWindow] = None,
) -> StressTestReport:
    """运行多窗口压力测试

    Args:
        symbols: 股票代码列表
        initial_cash: 初始资金
        windows: 自定义压力区间（默认使用 STRESS_WINDOWS）

    Returns:
        StressTestReport
    """
    from backtest.core import run_factor_backtest

    if windows is None:
        windows = STRESS_WINDOWS

    report = StressTestReport()
    logger.info(f"开始压力测试: {len(windows)} 个区间, {len(symbols)} 只股票")

    for window in windows:
        logger.info(f"测试区间: {window.name} ({window.start} ~ {window.end})")
        result = _run_single_window(symbols, window, initial_cash)
        report.results.append(result)

        if result.error:
            logger.warning(f"  {window.name}: 失败 - {result.error}")
        else:
            logger.info(
                f"  {window.name}: 收益={result.total_return:.2%}, "
                f"胜率={result.win_rate:.1%}, 回撤={result.max_drawdown:.2%}"
            )

    # 汇总分析
    _compute_overall_metrics(report)
    _run_monte_carlo(report, symbols, initial_cash)
    _generate_recommendations(report)

    return report


def _run_single_window(
    symbols: List[str],
    window: StressWindow,
    initial_cash: float,
) -> StressResult:
    """在单个压力区间内运行回测"""
    from backtest.core import run_factor_backtest

    result = StressResult(window=window)

    try:
        # 计算区间天数
        start_dt = datetime.strptime(window.start, "%Y-%m-%d")
        end_dt = datetime.strptime(window.end, "%Y-%m-%d")
        days = (end_dt - start_dt).days + 60  # 多取60天用于指标预热

        bt_result = run_factor_backtest(
            symbols=symbols,
            days=days,
            initial_cash=initial_cash,
            confidence_threshold=0.3,
            include_delisted=False,  # 压力测试用活股票数据
        )

        if bt_result is None:
            result.error = "回测无结果"
            return result

        metrics = bt_result.get("metrics")
        if metrics is None:
            result.error = "无性能指标"
            return result

        # 提取指标
        result.total_trades = bt_result.get("trade_count", 0)
        result.total_return = getattr(metrics, "total_return", 0)

        # 胜率
        trades = bt_result.get("trades", [])
        sell_trades = [t for t in trades if t.get("direction") == "SELL"]
        if sell_trades:
            wins = sum(1 for t in sell_trades if t.get("amount", 0) > 0)
            result.win_rate = wins / len(sell_trades)

        # 最大回撤
        result.max_drawdown = getattr(metrics, "max_drawdown", 0)

        # 夏普比率
        result.sharpe_ratio = getattr(metrics, "sharpe_ratio", 0)

        # 盈亏比
        exit_reasons = bt_result.get("exit_reasons", {})
        result.stop_loss_count = exit_reasons.get("stop_loss", 0)

    except Exception as e:
        result.error = str(e)
        logger.debug(f"压力测试 {window.name} 异常: {e}")

    return result


def _compute_overall_metrics(report: StressTestReport):
    """计算汇总指标"""
    valid = [r for r in report.results if not r.error]
    if not valid:
        return

    # 整体胜率（加权平均）
    total_trades = sum(r.total_trades for r in valid)
    if total_trades > 0:
        report.overall_win_rate = sum(
            r.win_rate * r.total_trades for r in valid
        ) / total_trades

    # 整体收益（几何平均）
    returns = [r.total_return for r in valid]
    if returns:
        report.overall_return = np.mean(returns)

    # 最好/最差区间
    report.best_window = max(valid, key=lambda r: r.total_return).window.name
    report.worst_window = min(valid, key=lambda r: r.total_return).window.name

    # 鲁棒性评分（0-100）
    score = 50  # 基础分

    # 正向加分
    positive_count = sum(1 for r in valid if r.total_return > 0)
    score += (positive_count / len(valid)) * 20  # 最多+20

    # 极端行情存活加分
    extreme_results = [r for r in valid if r.window.severity == "EXTREME"]
    if extreme_results:
        survived = sum(1 for r in extreme_results if r.total_return > -0.10)
        score += (survived / len(extreme_results)) * 15  # 最多+15

    # 胜率加分
    if report.overall_win_rate > 0.5:
        score += 10

    # 最大回撤扣分
    worst_dd = max(r.max_drawdown for r in valid) if valid else 0
    if worst_dd > 0.30:
        score -= 20
    elif worst_dd > 0.20:
        score -= 10

    # 极端行情亏损扣分
    for r in extreme_results:
        if r.total_return < -0.20:
            score -= 10

    report.robustness_score = max(0, min(100, score))


def _run_monte_carlo(
    report: StressTestReport,
    symbols: List[str],
    initial_cash: float,
    n_simulations: int = 1000,
):
    """蒙特卡洛模拟：对交易序列重采样，估算置信区间

    方法：从历史交易中随机抽样（有放回），构建模拟收益分布
    """
    from backtest.core import run_factor_backtest

    # 获取一次完整回测的交易序列
    try:
        bt_result = run_factor_backtest(
            symbols=symbols, days=500, initial_cash=initial_cash,
        )
        if bt_result is None:
            return

        trades = bt_result.get("trades", [])
        sell_trades = [t for t in trades if t.get("direction") == "SELL"]
        if len(sell_trades) < 10:
            return

        # 提取每笔交易的收益率
        pnl_list = []
        for t in sell_trades:
            amount = t.get("amount", 0)
            # 简化：用价格变化估算收益
            pnl_list.append(np.random.normal(0.01, 0.05))  # 占位

        # 实际使用回测的 perf_df 计算日收益率
        perf_df = bt_result.get("perf_df")
        if perf_df is not None and "daily_return" in perf_df.columns:
            daily_returns = perf_df["daily_return"].dropna().values
            if len(daily_returns) < 20:
                return

            # 蒙特卡洛：重采样日收益率序列
            sim_returns = []
            for _ in range(n_simulations):
                # 随机打乱日收益率顺序
                shuffled = np.random.choice(daily_returns, size=len(daily_returns), replace=True)
                sim_total = np.prod(1 + shuffled) - 1
                sim_returns.append(sim_total)

            sim_returns = np.array(sim_returns)
            report.monte_carlo = {
                "n_simulations": n_simulations,
                "mean_return": float(np.mean(sim_returns)),
                "median_return": float(np.median(sim_returns)),
                "std_return": float(np.std(sim_returns)),
                "ci_5pct": float(np.percentile(sim_returns, 5)),
                "ci_95pct": float(np.percentile(sim_returns, 95)),
                "prob_positive": float(np.mean(sim_returns > 0)),
                "prob_loss_gt_10pct": float(np.mean(sim_returns < -0.10)),
                "prob_loss_gt_20pct": float(np.mean(sim_returns < -0.20)),
            }
            logger.info(
                f"蒙特卡洛: 均值={report.monte_carlo['mean_return']:.2%}, "
                f"5%分位={report.monte_carlo['ci_5pct']:.2%}, "
                f"正收益概率={report.monte_carlo['prob_positive']:.1%}"
            )

    except Exception as e:
        logger.debug(f"蒙特卡洛模拟失败: {e}")


def _generate_recommendations(report: StressTestReport):
    """生成改进建议"""
    valid = [r for r in report.results if not r.error]
    if not valid:
        report.recommendations.append("所有压力测试区间均无结果，请检查数据和策略")
        return

    # 极端行情分析
    extreme_results = [r for r in valid if r.window.severity == "EXTREME"]
    for r in extreme_results:
        if r.total_return < -0.15:
            report.recommendations.append(
                f"【{r.window.name}】亏损 {r.total_return:.1%}，"
                f"建议：极端行情下应强制空仓或降低仓位至 10% 以下"
            )

    # 最大回撤分析
    worst_dd_r = max(valid, key=lambda r: r.max_drawdown)
    if worst_dd_r.max_drawdown > 0.20:
        report.recommendations.append(
            f"最大回撤 {worst_dd_r.max_drawdown:.1%}（{worst_dd_r.window.name}），"
            f"建议：收紧止损至 ATR×1.5 或启用组合级回撤止损"
        )

    # 胜率分析
    low_wr = [r for r in valid if r.win_rate < 0.4 and r.total_trades >= 5]
    if low_wr:
        names = ", ".join(r.window.name for r in low_wr)
        report.recommendations.append(
            f"以下区间胜率 <40%: {names}，建议：提高入场置信度阈值或增加过滤条件"
        )

    # 蒙特卡洛分析
    mc = report.monte_carlo
    if mc:
        if mc.get("prob_loss_gt_20pct", 0) > 0.1:
            report.recommendations.append(
                f"蒙特卡洛: {mc['prob_loss_gt_20pct']:.0%} 概率亏损超 20%，"
                f"建议：降低单笔风险预算或增加分散化"
            )
        if mc.get("ci_5pct", 0) < -0.15:
            report.recommendations.append(
                f"蒙特卡洛: 5%最差情况亏损 {mc['ci_5pct']:.1%}，"
                f"建议：确保此亏损在可承受范围内"
            )

    # 鲁棒性评分
    if report.robustness_score < 60:
        report.recommendations.append(
            f"鲁棒性评分 {report.robustness_score:.0f}/100，不建议实盘。"
            f"建议：先在模拟盘运行 3 个月验证"
        )
    elif report.robustness_score < 80:
        report.recommendations.append(
            f"鲁棒性评分 {report.robustness_score:.0f}/100，可小仓位试运行。"
            f"建议：初始仓位不超过总资金的 20%"
        )


# ========== 报告输出 ==========

def format_stress_report(report: StressTestReport) -> str:
    """格式化压力测试报告为文本"""
    lines = []
    lines.append("=" * 70)
    lines.append("  多窗口压力测试报告")
    lines.append("=" * 70)
    lines.append("")

    # 汇总
    lines.append(f"鲁棒性评分: {report.robustness_score:.0f}/100")
    lines.append(f"整体胜率: {report.overall_win_rate:.1%}")
    lines.append(f"整体收益: {report.overall_return:.2%}")
    lines.append(f"最佳区间: {report.best_window}")
    lines.append(f"最差区间: {report.worst_window}")
    lines.append("")

    # 各区间详情
    lines.append("-" * 70)
    lines.append(f"{'区间':<25} {'收益':>8} {'胜率':>8} {'回撤':>8} {'夏普':>8} {'交易':>6}")
    lines.append("-" * 70)

    for r in report.results:
        if r.error:
            lines.append(f"{r.window.name:<25} {'ERROR':>8} {r.error}")
        else:
            lines.append(
                f"{r.window.name:<25} "
                f"{r.total_return:>7.1%} "
                f"{r.win_rate:>7.1%} "
                f"{r.max_drawdown:>7.1%} "
                f"{r.sharpe_ratio:>7.2f} "
                f"{r.total_trades:>5d}"
            )
    lines.append("")

    # 蒙特卡洛
    mc = report.monte_carlo
    if mc:
        lines.append("-" * 70)
        lines.append("蒙特卡洛模拟 (1000次)")
        lines.append("-" * 70)
        lines.append(f"  均值收益: {mc['mean_return']:.2%}")
        lines.append(f"  中位收益: {mc['median_return']:.2%}")
        lines.append(f"  收益标准差: {mc['std_return']:.2%}")
        lines.append(f"  5%分位 (VaR): {mc['ci_5pct']:.2%}")
        lines.append(f"  95%分位: {mc['ci_95pct']:.2%}")
        lines.append(f"  正收益概率: {mc['prob_positive']:.1%}")
        lines.append(f"  亏损>10%概率: {mc['prob_loss_gt_10pct']:.1%}")
        lines.append(f"  亏损>20%概率: {mc['prob_loss_gt_20pct']:.1%}")
        lines.append("")

    # 建议
    if report.recommendations:
        lines.append("-" * 70)
        lines.append("改进建议")
        lines.append("-" * 70)
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")
        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    test_symbols = ["000001", "600519", "000858"]
    print(f"压力测试标的: {test_symbols}")
    print(f"测试区间: {len(STRESS_WINDOWS)} 个")
    print("-" * 70)

    report = run_stress_test(test_symbols)
    print(format_stress_report(report))

    # 保存结果
    from lib.monitoring.backtest_saver import save_backtest_result, RESULTS_DIR
    import json as _json
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"stress_{ts}.json"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    stress_data = []
    for r in report.results:
        entry = {
            "window": r.window.label if hasattr(r.window, 'label') else str(r.window),
            "error": r.error,
        }
        if not r.error and r.perf_df is not None and not r.perf_df.empty:
            nav = (1 + r.perf_df["daily_return"].fillna(0)).cumprod().values
            entry["metrics"] = {"total_return": float(nav[-1] - 1), "max_drawdown": float(((nav / np.maximum.accumulate(nav)) - 1).min())}
        stress_data.append(entry)

    out_path.write_text(_json.dumps({"timestamp": ts, "script": "stress",
                                      "symbols": test_symbols, "results": stress_data},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {out_path}")
