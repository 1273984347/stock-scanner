"""每日扫描脚本 —— 可配合 cron/Task Scheduler 定时运行

功能：
1. 扫描候选股信号
2. 检查持仓退出条件
3. 生成日报
4. 推送飞书

使用方式：
    python scripts/daily_scan.py                    # 单次运行
    python scripts/daily_scan.py --no-push          # 不推送飞书
    python scripts/daily_scan.py --symbols 600519   # 指定股票
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# 添加项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _is_trading_day() -> bool:
    """检查今天是否是交易日（通过 TDX 数据验证）"""
    import struct
    from pathlib import Path
    from datetime import datetime

    dt = datetime.now()
    # 周末直接跳过
    if dt.weekday() >= 5:
        return False

    # 检查 TDX 数据
    tdx_path = Path("D:/TDX/vipdoc/sh/lday_4/sh600000.day")
    if tdx_path.exists():
        date_int = int(dt.strftime("%Y%m%d"))
        try:
            with open(tdx_path, "rb") as f:
                raw = f.read()
                for i in range(0, len(raw), 32):
                    if struct.unpack_from("<I", raw, i)[0] == date_int:
                        return True
        except Exception:
            pass
        return False  # TDX 中没有今天数据 = 非交易日

    # 无 TDX 文件时仅跳过周末
    return True


def run_daily_scan(push_feishu: bool = True, extra_symbols: list = None):
    """执行每日扫描"""
    # 非交易日跳过
    if not _is_trading_day():
        from datetime import datetime
        print(f"{datetime.now().strftime('%Y-%m-%d')} 非交易日，跳过扫描")
        return None

    from lib.paper_trading_runner import PaperTradingRunner, TRADE_LOG_DIR
    from lib.signal_tracker import SignalTracker

    runner = PaperTradingRunner(initial_cash=1_000_000, preset="balanced")
    report = runner.run_once()

    # 记录信号到追踪器
    if report.signals:
        tracker = SignalTracker()
        tracker.record_batch(report.signals)
        tracker.update_actual_returns()
        eval_result = tracker.evaluate_signals(days=30)
        logger.info(
            f"信号追踪: 记录{len(report.signals)}条, "
            f"历史准确率{eval_result['accuracy']:.1%}, "
            f"盈亏比{eval_result['profit_loss_ratio']:.2f}"
        )

    # Borrow 1 production caller: 多风格投票 + past_context 注入
    try:
        from lib.panel_decision import get_panel_decision, format_panel_decision
        from lib.data_layer import get_stock_hist_multi_source
        from lib.strategies import get_data_factors
        if report.signals:
            for s in report.signals[:5]:  # 只对 top 5 候选股投票 (避免性能 regression)
                code = s.get("code", "")
                if not code:
                    continue
                try:
                    hist_df = get_stock_hist_multi_source(code, days=60)
                    if hist_df.empty:
                        continue
                    factors = get_data_factors(code) if hasattr(get_data_factors, '__call__') else {}
                    decision = get_panel_decision(code, hist_df, data_factors=factors)
                    if "past_context" in decision:
                        logger.info(f"{code} 多风格投票: {decision['signal']} ({decision['confidence']:.0%}) 含历史反思")
                    else:
                        logger.info(f"{code} 多风格投票: {decision['signal']} ({decision['confidence']:.0%})")
                except Exception as e:
                    logger.debug(f"{code} panel vote 失败: {e}")
    except Exception as e:
        logger.debug(f"panel_decision 导入失败: {e}")

    # 财报预警：持仓股财报前3天推送
    try:
        from lib.data_earnings import get_upcoming_earnings
        upcoming = get_upcoming_earnings(days=3)
        if not upcoming.empty and report.positions:
            held_codes = {p['code'] for p in report.positions}
            earnings_alerts = [
                row for _, row in upcoming.iterrows()
                if row.get("股票代码") in held_codes
            ]
            if earnings_alerts:
                alert_lines = ["## ⚠️ 财报预警（持仓股）", ""]
                for row in earnings_alerts:
                    alert_lines.append(
                        f"- {row.get('股票代码', '?')} {row.get('名称', '?')}: "
                        f"{row.get('预计披露日期', '?')} 发布财报"
                    )
                logger.warning(f"持仓股财报预警: {len(earnings_alerts)} 只")
                # 推送飞书
                if push_feishu:
                    try:
                        from reporter.feishu_pusher import push_report
                        push_report("\n".join(alert_lines), [])
                    except Exception:
                        pass
    except Exception as e:
        logger.debug(f"财报预警检查失败: {e}")

    # 使用富文本格式化报告
    try:
        from scripts.daily_report import format_daily_report

        # 获取市场状态（从缓存）
        market_state = None
        try:
            cache_file = ROOT / "cache" / "api_market.json"
            if cache_file.exists():
                market_state = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

        # 格式化报告
        formatted_report = format_daily_report(
            report=report,
            eval_result=eval_result if 'eval_result' in dir() else None,
            market_state=market_state,
        )

        print(formatted_report)

        # 推送飞书
        if push_feishu:
            try:
                from reporter.feishu_pusher import push_report
                push_report(formatted_report, [])
            except Exception as e:
                logger.warning(f"飞书推送失败: {e}")

    except Exception as e:
        logger.warning(f"报告格式化失败，使用基础格式: {e}")
        # 回退到基础格式
        print(f"\n{'='*50}")
        print(f"模拟盘日报 {report.date}")
        print(f"{'='*50}")
        print(f"总资产: {report.total_value:,.0f}")
        print(f"累计收益: {report.cumulative_return:.2%}")
        print(f"最大回撤: {report.max_drawdown:.2%}")
        print(f"胜率: {report.win_rate:.1%}")
        print(f"今日交易: {report.trades_today} 笔")
        print(f"平均滑点: {report.slippage_avg:.3%}")

        if report.positions:
            print(f"\n持仓:")
            for p in report.positions[:5]:
                print(f"  {p['code']}: {p['shares']}股 成本{p['cost']:.2f}")

        if report.signals:
            print(f"\n今日信号:")
            for s in report.signals:
                print(f"  {s['code']}: 置信度{s['confidence']:.2f}")

    print(f"\n报告已保存: {TRADE_LOG_DIR}/report_{datetime.now().strftime('%Y%m%d')}.json")

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--symbols", nargs="+")
    args = parser.parse_args()

    run_daily_scan(push_feishu=not args.no_push, extra_symbols=args.symbols)
