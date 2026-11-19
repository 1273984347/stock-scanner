"""告警文本构造（评审 P1-6 推送倒置，2026-11-20）

自 reporter/scan_notify.py 下沉：**文本构造是纯函数，属于 lib 层**；
推送通道走 ``lib/infra/push_sink.py``（reporter 注册 sink）。原位置保留
兼容 re-export（send_alert_message 与 contract 测试在用）。
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def build_alert_content(alert_type: str, alerts: list) -> str:
    """告警类消息的文本构造。

    P-79 遗留项（2026-11-14）：告警不走「策略名: N 只」统计形状，独立构造。
    """
    type_labels = {
        "ic_decay_alert": "IC 衰减告警",
        "ic_decay": "IC 衰减告警",
    }
    label = type_labels.get(alert_type, alert_type)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"【{label}】({now})",
        "=" * 30,
    ]
    if not alerts:
        lines.append("（无告警明细）")
    for a in alerts or []:
        if not isinstance(a, dict):
            lines.append(f"- {a}")
            continue
        severity = a.get("severity", "")
        message = a.get("message", "")
        prefix = f"[{severity}] " if severity else ""
        lines.append(f"- {prefix}{message}")
    lines.append("=" * 30)
    lines.append(f"共 {len(alerts or [])} 条告警")
    return "\n".join(lines)
