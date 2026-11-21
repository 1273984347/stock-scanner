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

    # 心跳自检（评审 P0-4，2026-11-20）：晨报是每日固定触点，任何一次运行都先
    # 检查上一段推送是否断流（P-79 断流 6 个月的对策：机器主动看 push_log），
    # 失联则把告警段插进报告一起推送。
    from reporter.heartbeat import check_push_heartbeat
    hb = check_push_heartbeat()
    if hb["stale"]:
        logger.warning("推送心跳失联: %s", hb["reason"])
        text = (f"【推送心跳告警】{hb['reason']}\n"
                f"若连续多日出现本告警，请检查调度任务与网络。\n\n") + text

    # 本地仓陈旧告警（增量评审 Δ2，2026-11-22）：data/store 是断网生存底座，
    # 整仓陈旧此前无任何运行时信号（/api/health 只看 cache）——晨报作为每日
    # 固定触点机器主动看，陈旧 ≥3 天的数据集前置告警段。
    try:
        from lib.data.local_store import staleness_report
        bad = [r for r in staleness_report() if r[1] != "ok"]
        if bad:
            lines = [f"【本地仓陈旧告警】{len(bad)} 个数据集异常（StoreDataSync 任务是否在跑？）"]
            lines += [f"- {ds}: {st} {det}" for ds, st, det in bad]
            text = "\n".join(lines) + "\n\n" + text
    except Exception as e:  # 告警面故障不得炸晨报
        logger.warning("本地仓新鲜度检查失败: %s", e)

    ok = feishu_pusher.push_morning_report(text, chart_paths)
    if not ok:
        print("[FALLBACK]", text)
    return ok
