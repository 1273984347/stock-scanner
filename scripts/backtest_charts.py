"""回测结果可视化 —— 生成静态图表（净值曲线/交易热力图/因子贡献度）

使用方式：
    python scripts/backtest_charts.py --symbols 600519,000858 --days 500
    python scripts/backtest_charts.py --from-cache cache/backtest_results/latest.json
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 添加项目根目录到 path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
import matplotlib.ticker as mticker

# 中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def plot_equity_curve(result: dict, output_path: str = "cache/charts/equity_curve.png"):
    """绘制净值曲线 + 回撤 + 交易标记"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    perf_df = result.get("perf_df")
    if perf_df is None:
        print("无 perf_df 数据")
        return

    if isinstance(perf_df, dict):
        perf_df = pd.DataFrame(perf_df)

    # 兼容多种列名格式
    date_col = next((c for c in ["date", "trade_date"] if c in perf_df.columns), perf_df.index)
    dates = pd.to_datetime(perf_df[date_col] if isinstance(date_col, str) else date_col)

    # 净值：优先用 total_value，其次 nav
    if "total_value" in perf_df.columns:
        tv = perf_df["total_value"].astype(float)
        nav = tv / tv.iloc[0] if tv.iloc[0] > 0 else tv
    else:
        nav = perf_df.get("nav", perf_df.get("portfolio_nav", pd.Series([1.0])))

    benchmark = perf_df.get("benchmark_nav", perf_df.get("hs300_nav", pd.Series([1.0] * len(nav))))

    # 计算回撤
    peak = nav.expanding().max()
    drawdown = (nav - peak) / peak

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), height_ratios=[3, 1], sharex=True)
    fig.suptitle('策略净值曲线', fontsize=16, fontweight='bold')

    # 净值曲线
    ax1.plot(dates, nav, label='策略净值', color='#4f8cff', linewidth=2)
    ax1.plot(dates, benchmark, label='基准(沪深300)', color='#aaa', linestyle='--', linewidth=1.5)
    ax1.fill_between(dates, nav, alpha=0.08, color='#4f8cff')
    ax1.set_ylabel('净值')
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)

    # 交易标记
    trades = result.get("trades", [])
    if trades:
        for t in trades:
            entry_date = pd.to_datetime(t.get("entry_date", ""))
            exit_date = pd.to_datetime(t.get("exit_date", ""))
            pnl = t.get("pnl_pct", 0)
            color = '#50c878' if pnl >= 0 else '#dc5050'
            if entry_date in dates.values:
                ax1.axvline(entry_date, color=color, alpha=0.2, linewidth=0.5)
            if exit_date in dates.values:
                marker = 'o' if pnl >= 0 else 'v'
                ax1.scatter(exit_date, nav.iloc[-1], color=color, marker=marker, s=30, zorder=5)

    # 回撤
    ax2.fill_between(dates, drawdown * 100, 0, color='#dc5050', alpha=0.3)
    ax2.plot(dates, drawdown * 100, color='#dc5050', linewidth=1)
    ax2.set_ylabel('回撤(%)')
    ax2.set_xlabel('日期')
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.xticks(rotation=45)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"净值曲线已保存: {output_path}")


def plot_trade_heatmap(result: dict, output_path: str = "cache/charts/trade_heatmap.png"):
    """交易热力图 —— 持仓时间段 + 盈亏分布"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    trades = result.get("trades", [])
    if not trades:
        print("无交易数据")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle('交易分析', fontsize=16, fontweight='bold')

    # 盈亏分布
    pnls = [t.get("pnl_pct", 0) * 100 for t in trades]
    colors = ['#50c878' if p >= 0 else '#dc5050' for p in pnls]
    ax1.bar(range(len(pnls)), pnls, color=colors, alpha=0.7)
    ax1.axhline(0, color='black', linewidth=0.5)
    ax1.set_xlabel('交易序号')
    ax1.set_ylabel('收益率(%)')
    ax1.set_title('逐笔盈亏')
    ax1.grid(True, alpha=0.3, axis='y')

    # 持仓天数 vs 收益散点图
    hold_days = [t.get("hold_days", 0) for t in trades]
    ax2.scatter(hold_days, pnls, c=colors, alpha=0.6, s=50)
    ax2.axhline(0, color='black', linewidth=0.5)
    ax2.set_xlabel('持仓天数')
    ax2.set_ylabel('收益率(%)')
    ax2.set_title('持仓天数 vs 收益')
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"交易热力图已保存: {output_path}")


def plot_factor_contribution(result: dict, output_path: str = "cache/charts/factor_contribution.png"):
    """因子贡献度分析 —— 哪些因子贡献了最多收益"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    trades = result.get("trades", [])
    if not trades:
        print("无交易数据")
        return

    # 按退出原因分类统计
    exit_reasons = result.get("exit_reasons", {})
    if not exit_reasons:
        print("无退出原因数据")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle('因子贡献度分析', fontsize=16, fontweight='bold')

    # 退出原因饼图
    labels = list(exit_reasons.keys())
    sizes = list(exit_reasons.values())
    label_map = {'stop_loss': '止损', 'take_profit': '止盈', 'trailing_stop': '移动止损',
                 'ema_exit': 'EMA退出', 'timeout': '超时', 'partial_exit': '分仓止盈', 'other': '其他'}
    display_labels = [label_map.get(l, l) for l in labels]

    colors_pie = ['#dc5050', '#50c878', '#ff9800', '#9c27b0', '#607d8b', '#00bcd4', '#795548']
    ax1.pie(sizes, labels=display_labels, autopct='%1.1f%%', colors=colors_pie[:len(sizes)])
    ax1.set_title('退出原因分布')

    # 月度收益柱状图
    if trades:
        monthly_returns = {}
        for t in trades:
            exit_date = t.get("exit_date", "")
            if exit_date:
                month = exit_date[:7]  # YYYY-MM
                pnl = t.get("pnl_pct", 0) * 100
                monthly_returns[month] = monthly_returns.get(month, 0) + pnl

        if monthly_returns:
            months = sorted(monthly_returns.keys())
            returns = [monthly_returns[m] for m in months]
            colors_bar = ['#50c878' if r >= 0 else '#dc5050' for r in returns]
            ax2.bar(range(len(months)), returns, color=colors_bar, alpha=0.7)
            ax2.set_xticks(range(len(months)))
            ax2.set_xticklabels(months, rotation=45, fontsize=9)
            ax2.axhline(0, color='black', linewidth=0.5)
            ax2.set_ylabel('累计收益(%)')
            ax2.set_title('月度收益')
            ax2.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"因子贡献度图已保存: {output_path}")


def generate_all_charts(result: dict, output_dir: str = "cache/charts"):
    """生成所有图表"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    plot_equity_curve(result, f"{output_dir}/equity_curve.png")
    plot_trade_heatmap(result, f"{output_dir}/trade_heatmap.png")
    plot_factor_contribution(result, f"{output_dir}/factor_contribution.png")
    print(f"\n所有图表已保存到: {output_dir}/")


def main():
    parser = argparse.ArgumentParser(description="回测结果可视化")
    parser.add_argument("--symbols", type=str, help="股票代码，逗号分隔")
    parser.add_argument("--days", type=int, default=500, help="回测天数")
    parser.add_argument("--from-cache", type=str, help="从缓存JSON文件加载结果")
    parser.add_argument("--output-dir", type=str, default="cache/charts", help="输出目录")
    args = parser.parse_args()

    if args.from_cache:
        with open(args.from_cache, "r", encoding="utf-8") as f:
            result = json.load(f)
        generate_all_charts(result, args.output_dir)
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
        from backtest.core import run_factor_backtest
        result = run_factor_backtest(symbols, days=args.days)
        if result:
            generate_all_charts(result, args.output_dir)
        else:
            print("回测无结果")
    else:
        print("请指定 --symbols 或 --from-cache")


if __name__ == "__main__":
    main()
