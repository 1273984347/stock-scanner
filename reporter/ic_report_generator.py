"""IC 报告推送 — 复用 reporter 飞书渠道

push_ic_report(date: str = None) -> bool:
    生成 IC 报告并推送至飞书
    触发时机：
        - 每周一 09:30（常规）
        - 每月 1 日 09:30（月度）
        - IC < -0.10 时（紧急，不等周期）

push_emergency_alert(factor: str, ic_value: float) -> bool:
    紧急告警：IC 单日 < -0.10
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def push_ic_report(date: str = None) -> bool:
    """生成并推送 IC 报告"""
    from lib.portfolio.ic_reporter import build_ic_report
    from reporter.feishu_pusher import push_report

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    try:
        report_text = build_ic_report(date)
    except Exception as e:
        logger.error(f"[ic_report_generator] 生成 IC 报告失败: {e}")
        return False

    try:
        content = build_ic_push_content(report_text)
        push_report(content, chart_paths=[])
        logger.info(f"[ic_report_generator] IC 报告推送成功")
        return True
    except Exception as e:
        logger.error(f"[ic_report_generator] 飞书推送失败: {e}")
        return False


def push_emergency_alert(factor: str, ic_value: float) -> bool:
    """紧急告警：IC < -0.10"""
    from reporter.feishu_pusher import push_report

    content = build_emergency_content(factor, ic_value)
    try:
        push_report(content, chart_paths=[])
        logger.warning(f"[ic_report_generator] 紧急告警已推送: {factor} IC={ic_value}")
        return True
    except Exception as e:
        logger.error(f"[ic_report_generator] 紧急告警推送失败: {e}")
        return False


def build_ic_push_content(report_text: str) -> str:
    """构建 IC 推送内容"""
    return report_text


def build_emergency_content(factor: str, ic_value: float) -> str:
    """构建紧急告警内容"""
    return (
        f"⚠️ IC 紧急告警\n"
        f"因子: {factor}\n"
        f"单日 IC: {ic_value:.4f}（阈值: -0.10）\n"
        f"已自动降权 50%，请人工核实"
    )
