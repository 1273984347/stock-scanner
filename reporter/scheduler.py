"""调度逻辑 — 交易日判断 + 错误处理"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def is_trading_day() -> bool:
    """判断今天是否为 A 股交易日"""
    today = datetime.now()
    # 周末不是
    if today.weekday() >= 5:
        return False
    return True


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
