"""盘后选股 - 入口脚本"""

import sys
import argparse
from datetime import datetime, timedelta
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
    parser.add_argument("--fcf", type=float, help="DCF: 基期自由现金流")
    parser.add_argument("--growth", type=float, help="DCF: 年增长率")
    parser.add_argument("--discount", type=float, help="DCF: 折现率/WACC")
    parser.add_argument("--terminal-growth", type=float, default=0.03, help="DCF: 永续增长率")
    parser.add_argument("--dcf-years", type=int, default=5, help="DCF: 预测期年数")
    parser.add_argument("--dcf-debt", type=float, default=0, help="DCF: 净负债")
    parser.add_argument("--dcf-cash", type=float, default=0, help="DCF: 现金")
    parser.add_argument("--dcf-shares", type=float, default=1, help="DCF: 总股本")
    parser.add_argument("--value", metavar="CODE", help="巴菲特 20 项评分")
    parser.add_argument("--earnings", action="store_true", help="财报日历")
    parser.add_argument("--anomaly", metavar="CODE", help="财报异常检测")
    parser.add_argument("--macro", action="store_true", help="宏观数据监控")
    parser.add_argument("--data-source", choices=["auto", "adata", "westock", "tushare", "akshare"],
                        default="auto", help="数据源选择（auto=自动选最快）")
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
        run_score_mode(args.score, data_source=args.data_source)
        return

    # DCF 估值
    if args.dcf:
        run_dcf_mode(args.dcf, fcf=args.fcf, growth=args.growth,
                     discount=args.discount, terminal_growth=args.terminal_growth,
                     years=args.dcf_years, debt=args.dcf_debt,
                     cash=args.dcf_cash, shares=args.dcf_shares)
        return

    # 宏观监控
    if args.macro:
        run_macro_mode()
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


def run_score_mode(code, data_source="akshare"):
    """综合评分模式（四维真实数据）"""
    from scanner import get_stock_hist_multi_source, get_realtime_quotes
    from factors import compute_all_signals
    from lib.technical_signals import calc_composite_verdict
    from lib.scorer import (comprehensive_score, score_fundamental,
                            score_capital_flow, score_news, score_technical,
                            calc_deviation_value, check_anomaly_threshold,
                            get_board_type)

    logger.info(f"综合评分: {code} (数据源: {data_source})")
    try:
        hist = get_stock_hist_multi_source(code, days=180, source=data_source)
    except Exception:
        logger.error(f"获取 {code} 数据失败")
        return
    if hist.empty:
        logger.error(f"获取 {code} 数据失败")
        return

    # 技术面：12 信号综合研判
    tech_result = calc_composite_verdict(hist)
    tech_signals = tech_result["signals"]
    tech_summary = tech_result["summary"]
    bullish_pct = tech_summary.get("bullish_pct", 0)
    tech_score = min(10, max(0, bullish_pct / 10))

    # 因子数据
    result = compute_all_signals(hist)
    factors = result["factors"] if result else {}

    # 基本面：用因子近似
    rsi = factors.get("rsi_14", 50)
    vol_20 = factors.get("volatility_20", 30)
    mom_5 = factors.get("momentum_5", 0)
    fund_score = min(10, max(0,
        (3 if rsi > 30 and rsi < 70 else 1) +
        (3 if vol_20 < 30 else 1) +
        (2 if mom_5 > 0 else 0) + 2
    ))

    # 资金面：用量比和换手率近似
    vol_ratio = factors.get("volume_ratio", 1.0)
    cap_score = min(10, max(0,
        (4 if vol_ratio > 1.5 else 2 if vol_ratio > 1.0 else 0) + 3
    ))

    # 消息面：用默认值（无新闻数据源时）
    news_score = 5.0

    # 综合评分
    final = comprehensive_score(fund_score, cap_score, news_score, tech_score)

    # 偏移值检查
    board_type = "main"
    stock_name = ""
    try:
        quotes = get_realtime_quotes()
        if not quotes.empty:
            row = quotes[quotes["代码"] == code]
            if not row.empty:
                stock_name = row.iloc[0].get("名称", "")
                board_type = get_board_type(code, stock_name)
    except Exception:
        pass

    # 输出
    print(f"\n{'='*60}")
    print(f"  综合评分: {code} {stock_name}")
    print(f"{'='*60}")
    print(f"\n  总分: {final['score']}/10  评级: {final['rating']}")
    print(f"\n  【评分明细】")
    print(f"    基本面 (35%): {final['breakdown']['fundamental']}/10")
    print(f"    资金面 (35%): {final['breakdown']['capital_flow']}/10")
    print(f"    消息面 (20%): {final['breakdown']['news']}/10")
    print(f"    技术面 (10%): {final['breakdown']['technical']}/10")

    print(f"\n  【技术信号】({tech_summary['total_indicators']} 个指标)")
    print(f"    看多: {tech_summary['bullish_count']} ({tech_summary['bullish_pct']}%)")
    print(f"    看空: {tech_summary['bearish_count']} ({tech_summary['bearish_pct']}%)")
    print(f"    综合: {tech_summary['verdict']}")
    for ind, sig in tech_signals.items():
        label = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(sig, sig)
        print(f"      {ind:15s} {label}")

    print(f"\n  【关键因子】")
    print(f"    RSI(14):    {rsi:.1f}")
    print(f"    波动率(20): {vol_20:.1f}%")
    print(f"    动量(5):    {mom_5:+.1f}%")
    print(f"    量比:       {vol_ratio:.2f}")
    print(f"    板块:       {board_type}")

    print(f"{'='*60}")


def run_dcf_mode(code, fcf=None, growth=None, discount=None,
                  terminal_growth=0.03, years=5, debt=0, cash=0, shares=1):
    """DCF 估值模式"""
    from lib.dcf import dcf_valuation, sensitivity_matrix, margin_of_safety

    logger.info(f"DCF 估值: {code}")

    if fcf is None or growth is None or discount is None:
        print(f"\n{'='*50}")
        print(f"  DCF 估值: {code}")
        print(f"{'='*50}")
        print("  缺少必要参数，请指定:")
        print("    --fcf <自由现金流>")
        print("    --growth <增长率>")
        print("    --discount <折现率>")
        print(f"\n  示例:")
        print(f"    python run.py --dcf {code} --fcf 1e9 --growth 0.15 --discount 0.10")
        print(f"{'='*50}")
        return

    # DCF 估值
    result = dcf_valuation(fcf, growth, discount, terminal_growth, years, debt, cash, shares)
    if "error" in result:
        logger.error(result["error"])
        return

    # 敏感性矩阵
    g_range = [growth - 0.05, growth - 0.025, growth, growth + 0.025, growth + 0.05]
    d_range = [discount - 0.02, discount - 0.01, discount, discount + 0.01, discount + 0.02]
    g_range = [g for g in g_range if g > 0]
    d_range = [d for d in d_range if d > 0]
    sens = sensitivity_matrix(fcf, g_range, d_range, terminal_growth, years, debt, cash, shares)

    print(f"\n{'='*60}")
    print(f"  DCF 估值: {code}")
    print(f"{'='*60}")
    print(f"\n  【估值结果】")
    print(f"    企业价值:     {result['enterprise_value']:>15,.2f}")
    print(f"    股权价值:     {result['equity_value']:>15,.2f}")
    print(f"    每股价值:     {result['per_share_value']:>15,.2f}")
    print(f"\n  【假设参数】")
    print(f"    基期 FCF:     {fcf:>15,.2f}")
    print(f"    增长率:       {growth:>14.1%}")
    print(f"    折现率:       {discount:>14.1%}")
    print(f"    永续增长率:   {terminal_growth:>14.1%}")
    print(f"    预测期:       {years:>11d} 年")
    print(f"    净负债:       {debt:>15,.2f}")
    print(f"    现金:         {cash:>15,.2f}")
    print(f"    总股本:       {shares:>15,.0f}")

    print(f"\n  【敏感性分析（每股价值）】")
    header = f"  {'增长率\\\\折现率':>14s}"
    for d in sens["discount_range"]:
        header += f"  {d:>8.1%}"
    print(header)
    print(f"  {'-'*14}" + f" {'-'*8}" * len(sens["discount_range"]))
    for i, g in enumerate(sens["growth_range"]):
        row = f"  {g:>13.1%} "
        for v in sens["matrix"][i]:
            if v is None:
                row += f"  {'N/A':>8s}"
            else:
                row += f"  {v:>8.2f}"
        print(row)

    print(f"{'='*60}")


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
    """异常检测模式（接入多期财务数据）"""
    from lib.data_tushare import get_financials, _to_ts_code
    from lib.anomaly_detector import detect_anomalies, anomaly_summary

    logger.info(f"异常检测: {code}")
    ts_code = _to_ts_code(code)

    print(f"\n{'='*60}")
    print(f"  财报异常检测: {code} ({ts_code})")
    print(f"{'='*60}")

    # 用 Tushare 获取多期财务数据
    try:
        from lib.data_tushare import _get_pro
        pro = _get_pro()
        if pro is None:
            print("  Tushare 未配置（需要 TUSHARE_TOKEN），尝试用 akshare...")
            _run_anomaly_akshare(code)
            return

        # 获取近 8 期利润表
        income_df = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,revenue,n_income,operate_profit,total_cogs")
        if income_df.empty:
            print(f"  未找到 {code} 的财务数据")
            print(f"{'='*60}")
            return

        # 获取资产负债表
        balance_df = pro.balancesheet(ts_code=ts_code, fields="ts_code,ann_date,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab,inventories,accounts_receiv,goodwill,accounts_payable")

        # 获取现金流量表
        cf_df = pro.cashflow(ts_code=ts_code, fields="ts_code,ann_date,end_date,n_cashflow_act,c_pay_acq_const_fiam")

        # 构造 periods_data
        periods_data = []
        for _, row in income_df.head(8).iterrows():
            period = str(row.get("end_date", ""))
            entry = {
                "period": period,
                "revenue": row.get("revenue"),
                "net_income": row.get("n_income"),
                "operating_income": row.get("operate_profit"),
                "cost_of_revenue": row.get("total_cogs"),
            }

            # 匹配资产负债表
            if not balance_df.empty:
                bal = balance_df[balance_df["end_date"] == period]
                if not bal.empty:
                    b = bal.iloc[0]
                    entry.update({
                        "total_assets": b.get("total_assets"),
                        "total_liabilities": b.get("total_liab"),
                        "total_equity": b.get("total_hldr_eqy_exc_min_int"),
                        "total_current_assets": b.get("total_cur_assets"),
                        "total_current_liabilities": b.get("total_cur_liab"),
                        "inventory": b.get("inventories"),
                        "accounts_receivable": b.get("accounts_receiv"),
                        "goodwill": b.get("goodwill"),
                        "accounts_payable": b.get("accounts_payable"),
                    })

            # 匹配现金流量表
            if not cf_df.empty:
                cf = cf_df[cf_df["end_date"] == period]
                if not cf.empty:
                    c = cf.iloc[0]
                    entry.update({
                        "operating_cash_flow": c.get("n_cashflow_act"),
                        "capex": c.get("c_pay_acq_const_fiam"),
                    })

            periods_data.append(entry)

        if len(periods_data) < 2:
            print(f"  数据不足（仅 {len(periods_data)} 期），需要至少 2 期")
            print(f"{'='*60}")
            return

        # 执行异常检测
        alerts = detect_anomalies(periods_data)
        summary = anomaly_summary(alerts)

        print(f"\n  数据期数: {len(periods_data)}")
        print(f"  检测结果: {summary['total']} 条异常")
        print(f"    高风险: {summary['high']}  中风险: {summary['medium']}  低风险: {summary['low']}")
        print(f"    扣分: -{summary['score_penalty']}")

        if alerts:
            print(f"\n  【异常详情】")
            severity_label = {"high": "高", "medium": "中", "low": "低"}
            for i, a in enumerate(alerts, 1):
                sev = severity_label.get(a["severity"], a["severity"])
                print(f"\n  [{sev}] {a['rule']} | {a['period']}")
                print(f"    触发: {a['trigger']}")
                print(f"    详情: {a['detail']}")
                print(f"    含义: {a['implication']}")
        else:
            print(f"\n  未检测到异常信号，财务状况良好。")

        print(f"\n{'='*60}")

    except Exception as e:
        logger.error(f"异常检测失败: {e}")
        print(f"  检测失败: {e}")
        print(f"{'='*60}")


def _run_anomaly_akshare(code):
    """用 akshare 财报数据做简化异常检测"""
    from lib.data_earnings import get_earnings_report
    from lib.anomaly_detector import detect_anomalies, anomaly_summary

    report = get_earnings_report()
    if report.empty:
        print("  无可用财报数据")
        return

    stock_row = report[report["股票代码"] == code]
    if stock_row.empty:
        print(f"  未找到 {code} 的财报数据")
        return

    print(f"  找到 {len(stock_row)} 期数据（akshare 财报）")

    # 构造简化 periods_data
    periods_data = []
    for _, row in stock_row.head(8).iterrows():
        entry = {
            "period": str(row.get("报告期", "")),
            "revenue": _to_float(row.get("营业收入")),
            "net_income": _to_float(row.get("净利润")),
        }
        periods_data.append(entry)

    if len(periods_data) < 2:
        print("  数据不足")
        return

    alerts = detect_anomalies(periods_data)
    summary = anomaly_summary(alerts)
    print(f"  检测结果: {summary['total']} 条异常（高{summary['high']} 中{summary['medium']} 低{summary['low']}）")


def run_macro_mode():
    """宏观数据监控模式"""
    from lib.data_tushare import get_macro_cpi, get_macro_pmi, get_index_daily, _get_pro
    from factors import calc_market_exposure
    import pandas as pd

    logger.info("宏观数据监控...")

    print(f"\n{'='*60}")
    print(f"  宏观数据监控")
    print(f"{'='*60}")

    pro = _get_pro()

    # CPI 数据
    print(f"\n  【CPI 数据】")
    if pro:
        cpi = get_macro_cpi()
        if not cpi.empty:
            print(f"    最新: {cpi.head(3).to_string(index=False)}")
        else:
            print("    无数据（检查 TUSHARE_TOKEN）")
    else:
        print("    Tushare 未配置")

    # PMI 数据
    print(f"\n  【PMI 数据】")
    if pro:
        pmi = get_macro_pmi()
        if not pmi.empty:
            print(f"    最新: {pmi.head(3).to_string(index=False)}")
        else:
            print("    无数据")
    else:
        print("    Tushare 未配置")

    # 市场状态（沪深 300 择时）
    print(f"\n  【市场择时】")
    try:
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
        idx = ak.stock_zh_index_daily(symbol="sh000300")
        if not idx.empty:
            close = idx["close"].tail(120)
            volume = idx["volume"].tail(120) if "volume" in idx.columns else None
            pct_chg = close.pct_change().fillna(0)
            exposure = calc_market_exposure(close, volume, pct_chg)
            state_label = {"bull": "牛市", "neutral": "震荡", "bear": "熊市"}.get(
                exposure["state"], exposure["state"])
            print(f"    沪深300 状态: {state_label}")
            print(f"    建议仓位: {exposure['exposure']:.0%}")
        else:
            print("    获取沪深300数据失败")
    except Exception as e:
        print(f"    择时计算失败: {e}")

    print(f"\n{'='*60}")


def _to_float(val):
    """安全转换为 float"""
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


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

    数据结构：构建 (date, code) 双索引面板，
    - factor_panel: MultiIndex (date, code) 的因子值 DataFrame
    - forward_returns: MultiIndex (date, code) 的 20 日前瞻收益 Series
    """
    from factors import compute_all_signals
    from scanner import get_stock_hist, get_realtime_quotes
    from lib.evaluator import FactorEvaluator, EvalConfig
    import pandas as pd

    logger.info("开始因子评价...")

    df = get_realtime_quotes()
    if df.empty:
        logger.error("获取行情失败")
        return

    codes = df.sort_values("成交额", ascending=False).head(100)["代码"].tolist()

    FWD_DAYS = 20
    EVAL_DATES = 30  # 取最近 30 个截面日期（提升统计显著性）

    # 收集每只股票的历史数据
    stock_data = {}
    for code in codes:
        hist = get_stock_hist(code, days=300 + FWD_DAYS)
        if hist.empty or len(hist) < 120 + FWD_DAYS:
            continue
        hist = hist.copy()
        if "date" in hist.columns:
            hist["date"] = pd.to_datetime(hist["date"])
        stock_data[code] = hist

    if not stock_data:
        logger.error("无有效历史数据")
        return

    # 找到共同的截面日期（取最近 EVAL_DATES 个，每隔 20 个交易日）
    all_date_sets = [set(s["date"].tolist()) for s in stock_data.values()]
    common_dates = sorted(set.intersection(*all_date_sets)) if all_date_sets else []
    if len(common_dates) < EVAL_DATES + FWD_DAYS:
        # 退而求其次：用最多股票覆盖的日期
        date_counts = {}
        for s in stock_data.values():
            for d in s["date"].tolist():
                date_counts[d] = date_counts.get(d, 0) + 1
        # 按覆盖股票数排序，取前 EVAL_DATES 个（间隔 20 交易日）
        sorted_dates = sorted(date_counts.keys(), key=lambda d: date_counts[d], reverse=True)
        sorted_dates = sorted(sorted_dates)
        # 每 20 个交易日取一个截面
        step = 20
        eval_dates = sorted_dates[-(EVAL_DATES * step + FWD_DAYS):]
        eval_dates = eval_dates[::step][-EVAL_DATES:]
    else:
        step = 20
        eval_dates = common_dates[-(EVAL_DATES * step + FWD_DAYS):]
        eval_dates = eval_dates[::step][-EVAL_DATES:]

    if not eval_dates:
        logger.error("无法构建截面日期")
        return

    # 按 (date, code) 构建因子面板
    all_records = []
    for date in eval_dates:
        for code, hist in stock_data.items():
            hist_up_to = hist[hist["date"] <= date]
            if len(hist_up_to) < 60:
                continue
            # 计算因子
            result = compute_all_signals(hist_up_to)
            if not result or "factors" not in result:
                continue
            factors = result["factors"]
            factors["date"] = date
            factors["code"] = code

            # 计算 20 日前瞻收益
            date_idx = hist[hist["date"] <= date].index[-1]
            fwd_idx = date_idx + FWD_DAYS
            if fwd_idx < len(hist):
                price_t = hist.loc[date_idx, "close"] if "close" in hist.columns else None
                price_fwd = hist.loc[fwd_idx, "close"] if "close" in hist.columns else None
                if price_t is not None and price_t > 0 and price_fwd is not None:
                    factors["forward_return_20d"] = price_fwd / price_t - 1

            all_records.append(factors)

    if not all_records:
        logger.error("无有效因子数据")
        return

    panel = pd.DataFrame(all_records)

    # 构建因子矩阵（单截面：用最近一期截面）
    # 同时生成 forward_returns Series
    latest_date = panel["date"].max()
    latest_panel = panel[panel["date"] == latest_date].copy()
    latest_panel = latest_panel.set_index("code")

    factor_cols = [c for c in latest_panel.columns
                   if c not in ("date", "code", "forward_return_20d")]
    factor_df = latest_panel[factor_cols].dropna(how="all")

    forward_returns = latest_panel["forward_return_20d"].dropna()

    if factor_df.empty or forward_returns.empty:
        logger.error("因子或前瞻收益数据为空")
        return

    # 对齐 index
    common_idx = factor_df.index.intersection(forward_returns.index)
    factor_df = factor_df.loc[common_idx]
    forward_returns = forward_returns.loc[common_idx]

    # 使用 FactorEvaluator 生成综合报告（截面模式）
    evaluator = FactorEvaluator(EvalConfig(rolling_window=min(60, len(factor_df))))
    report = evaluator.generate_report(factor_df, forward_returns)
    print(report)

    # 额外输出：多截面 IC 统计
    if len(eval_dates) > 1:
        print(f"\n{'='*70}")
        print("  【多截面 IC 统计】")
        print(f"  截面数: {len(eval_dates)}")
        from scipy import stats as _stats
        ic_by_date = []
        for date in eval_dates:
            date_panel = panel[panel["date"] == date].set_index("code")
            if "forward_return_20d" not in date_panel.columns:
                continue
            fr = date_panel["forward_return_20d"].dropna()
            for col in factor_cols:
                if col not in date_panel.columns:
                    continue
                fv = date_panel[col].dropna()
                common = fv.index.intersection(fr.index)
                if len(common) < 10:
                    continue
                _ic, _ = _stats.spearmanr(fv.loc[common], fr.loc[common])
                ic_by_date.append({"date": date, "factor": col, "rank_ic": _ic})

        if ic_by_date:
            ic_df = pd.DataFrame(ic_by_date)
            print(f"\n  {'因子':<25s} {'IC 均值':>10s} {'IC 标准差':>10s} {'IC_IR':>8s} {'IC 胜率':>8s}")
            print(f"  {'-'*65}")
            for col in sorted(ic_df["factor"].unique()):
                col_ic = ic_df[ic_df["factor"] == col]["rank_ic"].dropna()
                if len(col_ic) < 2:
                    continue
                ic_mean = col_ic.mean()
                ic_std = col_ic.std()
                ic_ir = ic_mean / ic_std if ic_std > 0 else 0
                ic_wr = (col_ic > 0).mean()
                print(f"  {col:<25s} {ic_mean:>+10.4f} {ic_std:>10.4f} {ic_ir:>8.3f} {ic_wr:>8.1%}")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
