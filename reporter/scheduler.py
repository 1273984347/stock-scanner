"""调度逻辑 — 交易日判断 + 错误处理"""

import logging

logger = logging.getLogger(__name__)


def is_trading_day() -> bool:
    """判断今天是否为 A 股交易日（委托交易日历单一真源）。

    架构评审 P0 修复（2026-11-20，坑表 P-108）：旧实现只看周末，
    节假日晨报/策略报告照推上一交易日的陈旧数据。
    """
    from lib.data.trade_calendar import is_trading_day as _calendar_is_trading_day

    return _calendar_is_trading_day()


def send_strategy_report(results: dict, preset: str = "balanced") -> bool:
    """策略执行后报告入口（被 run.py 调用）"""
    from . import report_generator, chart_builder, feishu_pusher

    if not is_trading_day():
        logger.info("非交易日，跳过策略报告推送")
        return True

    chart_builder.cleanup_old_charts()
    text = report_generator.build_strategy_report(results, preset)
    chart_paths = chart_builder.build_all_charts(results)
    ok = feishu_pusher.push_report(text, chart_paths)
    if not ok:
        print("[FALLBACK]", text)
    return ok


def send_morning_report() -> bool:
    """晨间报告入口（被 run_daily.bat 调用）"""
    from . import report_generator, chart_builder, feishu_pusher

    if not is_trading_day():
        logger.info("非交易日，跳过晨间报告推送")
        return True

    chart_builder.cleanup_old_charts()
    text, chart_paths = report_generator.build_morning_report()
    ok = feishu_pusher.push_morning_report(text, chart_paths)
    if not ok:
        print("[FALLBACK]", text)
    return ok
