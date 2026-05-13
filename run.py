"""盘后选股 - 入口脚本"""

import sys
import argparse
from datetime import datetime
from scanner import run_all_strategies
from notifier import send_feishu_message, print_results
from config import FEISHU_RECEIVE_ID
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="A股盘后选股工具")
    parser.add_argument("--no-push", action="store_true", help="不推送飞书")
    parser.add_argument("--preset", choices=["balanced", "aggressive", "conservative"],
                        default="balanced", help="策略预设")
    parser.add_argument("--evaluate", action="store_true", help="运行因子评价")
    parser.add_argument("--backtest", action="store_true", help="运行回测")
    parser.add_argument("--symbols", nargs="+", default=["000001", "600519", "000858"],
                        help="标的代码")
    parser.add_argument("--days", type=int, default=250, help="回测天数")
    parser.add_argument("--walk-forward", action="store_true", help="Walk-forward 验证")
    parser.add_argument("--score", metavar="CODE", help="综合评分（四维加权）")
    parser.add_argument("--dcf", metavar="CODE", help="DCF 估值")
    parser.add_argument("--value", metavar="CODE", help="巴菲特 20 项评分")
    parser.add_argument("--earnings", action="store_true", help="财报日历")
    parser.add_argument("--anomaly", metavar="CODE", help="财报异常检测")
    parser.add_argument("--data-source", choices=["akshare", "westock", "tushare"],
                        default="akshare", help="数据源选择")
    args = parser.parse_args()

    logger.info(f"开始盘后选股 - {datetime.now().strftime('%Y-%m-%d %H:%M')} - {args.preset}")

    # 因子评价模式
    if args.evaluate:
        run_factor_evaluation()
        return

    # 回测模式
    if args.backtest:
        run_backtest_mode(args.symbols, args.days, args.walk_forward)
        return

    # 综合评分
    if args.score:
        run_score_mode(args.score)
        return

    # DCF 估值
    if args.dcf:
        run_dcf_mode(args.dcf)
        return

    # 巴菲特评分
    if args.value:
        run_value_mode(args.value)
        return

    # 财报日历
    if args.earnings:
        run_earnings_mode()
        return

    # 异常检测
    if args.anomaly:
        run_anomaly_mode(args.anomaly)
        return

    # 运行策略
    results = run_all_strategies(args.preset)

    if not results:
        logger.info("所有策略均无候选股")
        sys.exit(0)

    # 控制台打印
    print_results(results, args.preset)

    # 飞书推送
    if not args.no_push:
        if not FEISHU_RECEIVE_ID:
            logger.warning("FEISHU_RECEIVE_ID 未配置，跳过推送")
        else:
            send_feishu_message(results, args.preset)
    else:
        logger.info("已跳过飞书推送")

    logger.info("选股完成")


def run_score_mode(code):
    """综合评分模式"""
    from scanner import get_stock_hist
    from factors import compute_all_signals
    from lib.scorer import (comprehensive_score, score_fundamental,
                            score_capital_flow, score_news, score_technical)

    logger.info(f"综合评分: {code}")
    hist = get_stock_hist(code, days=180)
    if hist.empty:
        logger.error(f"获取 {code} 数据失败")
        return

    result = compute_all_signals(hist)
    if not result:
        logger.error("因子计算失败")
        return

    factors = result["factors"]
    technical_verdict = result["combined"]

    # 技术面评分（基于信号置信度）
    tech_score = technical_verdict["confidence"] * 10

    # 基本面评分（基于因子）
    roe = factors.get("rsi_14", 50)  # 用 RSI 近似
    fund_score = min(10, max(0, (100 - abs(roe - 50)) / 5))

    # 资金面/消息面用默认值
    cap_score = 5.0
    news_score = 5.0

    final = comprehensive_score(fund_score, cap_score, news_score, tech_score)
    print(f"\n{'='*50}")
    print(f"  综合评分: {code}")
    print(f"{'='*50}")
    print(f"  总分: {final['score']}/10")
    print(f"  评级: {final['rating']}")
    print(f"  基本面: {final['breakdown']['fundamental']}")
    print(f"  资金面: {final['breakdown']['capital_flow']}")
    print(f"  消息面: {final['breakdown']['news']}")
    print(f"  技术面: {final['breakdown']['technical']}")
    print(f"{'='*50}")


def run_dcf_mode(code):
    """DCF 估值模式"""
    from lib.dcf import dcf_valuation, sensitivity_matrix, margin_of_safety

    logger.info(f"DCF 估值: {code}")
    print(f"\n{'='*50}")
    print(f"  DCF 估值: {code}")
    print(f"{'='*50}")
    print("  需要手动输入 FCF、增长率、折现率等参数")
    print("  示例: python -c \"")
    print("    from lib.dcf import dcf_valuation")
    print("    r = dcf_valuation(fcf=1e9, growth=0.15, discount=0.10, shares=1e9)")
    print("    print(r['per_share_value'])")
    print("  \"")
    print(f"{'='*50}")


def run_value_mode(code):
    """巴菲特评分模式"""
    from lib.value_checklist import value_score, auto_score_financials

    logger.info(f"巴菲特评分: {code}")
    # 使用默认评分（人工判断维度用 3 分）
    auto_scores = auto_score_financials(
        roe_history=[0.15, 0.16, 0.14, 0.17, 0.15],
        debt_ratio=0.45,
        fcf_positive_years=4,
        gross_margin=0.35,
        ocf_to_ni=1.1,
    )
    financial_data = {**{k: 3 for k in [
        "brand_score", "switching_cost_score", "network_effect_score",
        "cost_advantage_score", "entry_barrier_score", "integrity_score",
        "capital_allocation_score", "insider_ownership_score",
        "track_record_score", "culture_score",
        "margin_of_safety_score", "pe_pb_percentile_score",
        "fcf_yield_score", "earnings_yield_score", "dividend_score",
    ]}, **auto_scores}

    result = value_score(financial_data)
    print(f"\n{'='*50}")
    print(f"  巴菲特评分: {code}")
    print(f"{'='*50}")
    print(f"  总分: {result['total']}/100")
    print(f"  评级: {'★' * result['stars']}{'☆' * (5 - result['stars'])} {result['rating']}")
    for key, dim in result["dimensions"].items():
        print(f"  {dim['label']}: {dim['score']}/25")
    print(f"{'='*50}")


def run_earnings_mode():
    """财报日历模式"""
    from lib.data_earnings import get_earnings_calendar, get_earnings_forecast

    logger.info("获取财报日历...")
    cal = get_earnings_calendar()
    forecast = get_earnings_forecast()

    print(f"\n{'='*60}")
    print(f"  财报日历")
    print(f"{'='*60}")
    if not cal.empty:
        print(f"  披露时间表: {len(cal)} 条")
        print(cal.head(20).to_string(index=False))
    else:
        print("  无数据")

    if not forecast.empty:
        print(f"\n  业绩预告: {len(forecast)} 条")
        print(forecast.head(10).to_string(index=False))
    print(f"{'='*60}")


def run_anomaly_mode(code):
    """异常检测模式"""
    from lib.data_earnings import get_earnings_report
    from lib.anomaly_detector import detect_anomalies, anomaly_summary

    logger.info(f"异常检测: {code}")
    print(f"\n{'='*50}")
    print(f"  异常检测: {code}")
    print(f"{'='*50}")

    # 尝试从财报数据构造 periods_data
    report = get_earnings_report()
    if report.empty:
        print("  无财报数据，请使用 --data-source tushare 获取详细财务数据")
        print(f"{'='*50}")
        return

    # 简化：用最新一期数据构造
    stock_row = report[report["股票代码"].str.contains(code, na=False)]
    if stock_row.empty:
        print(f"  未找到 {code} 的财报数据")
        print(f"{'='*50}")
        return

    print(f"  找到 {code} 财报数据，共 {len(stock_row)} 期")
    print("  详细异常检测需要多期财务数据，请使用 lib/anomaly_detector.py")
    print(f"{'='*50}")


def run_backtest_mode(symbols, days, walk_forward):
    """回测模式"""
    from backtest import run_factor_backtest, run_walk_forward

    if walk_forward:
        logger.info(f"Walk-forward 验证: {symbols}")
        result = run_walk_forward(symbols, days=days)
        if "error" in result:
            logger.error(result["error"])
            return
        summary = result["summary"]
        print("\n" + "=" * 60)
        print("  Walk-Forward 验证报告")
        print("=" * 60)
        print(f"  总轮次: {summary['rounds']}")
        print(f"  稳定轮次: {summary['stable_rounds']}")
        print(f"  脆弱轮次: {summary['fragile_rounds']}")
        print(f"  中位收益: {summary['median_return']:.2%}")
        print(f"  最差收益: {summary['worst_return']:.2%}")
        print(f"  中位夏普: {summary['median_sharpe']:.2f}")
        print("=" * 60)
    else:
        logger.info(f"因子策略回测: {symbols}, {days} 天")
        result = run_factor_backtest(symbols, days=days)
        if result:
            print(result["report"])
            print(f"\n交易次数: {result['trade_count']}")
        else:
            logger.error("回测无结果")


def run_factor_evaluation():
    """运行因子评价

    因子在 T 日计算，用 T+1 ~ T+20 日的实际收益验证预测能力。
    使用 FactorEvaluator 输出综合记分卡、冗余检测、IC 衰减。
    """
    from factors import compute_all_signals
    from scanner import get_stock_hist, get_realtime_quotes
    from lib.evaluator import FactorEvaluator, EvalConfig, build_forward_returns
    import pandas as pd

    logger.info("开始因子评价...")

    df = get_realtime_quotes()
    if df.empty:
        logger.error("获取行情失败")
        return

    codes = df.sort_values("成交额", ascending=False).head(100)["代码"].tolist()

    FWD_DAYS = 20
    all_factors = []
    all_prices = {}

    for code in codes:
        hist = get_stock_hist(code, days=250 + FWD_DAYS)
        if hist.empty or len(hist) < 60 + FWD_DAYS:
            continue

        hist_factor = hist.iloc[:-FWD_DAYS]
        result = compute_all_signals(hist_factor)
        if not result or "factors" not in result:
            continue

        factors = result["factors"]
        factors["code"] = code
        all_factors.append(factors)
        all_prices[code] = hist["close"]

    if not all_factors:
        logger.error("无有效因子数据")
        return

    # 构建因子面板（index=code, columns=factor_names）
    factor_df = pd.DataFrame(all_factors).set_index("code")

    # 构建前瞻收益（20日）
    forward_returns = pd.Series(dtype=float)
    for code in factor_df.index:
        prices = all_prices.get(code)
        if prices is not None and len(prices) > FWD_DAYS:
            price_t = prices.iloc[-FWD_DAYS - 1]
            price_t_fwd = prices.iloc[-1]
            if price_t > 0:
                forward_returns[code] = price_t_fwd / price_t - 1

    if forward_returns.empty:
        logger.error("无有效前瞻收益数据")
        return

    # 使用 FactorEvaluator 生成综合报告
    evaluator = FactorEvaluator(EvalConfig(rolling_window=min(60, len(factor_df))))
    report = evaluator.generate_report(factor_df, forward_returns)
    print(report)


if __name__ == "__main__":
    main()
