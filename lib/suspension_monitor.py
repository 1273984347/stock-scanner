"""停牌监控 —— 检测长期停牌并告警"""

import logging
from typing import Dict, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def check_suspension_duration(
    positions: List[Dict],
    market_data: Dict = None,
    threshold_days: int = 20,
) -> List[Dict]:
    """检查持仓中是否有长期停牌股票

    Args:
        positions: 持仓列表
        market_data: {code: DataFrame} 行情数据
        threshold_days: 停牌天数阈值（默认20天）

    Returns:
        停牌风险告警列表
    """
    alerts = []

    for pos in positions:
        code = pos.get("code", "")

        if market_data and code in market_data:
            df = market_data[code]
            if df is not None and not df.empty:
                # 检查最近是否有成交量
                recent = df.tail(threshold_days + 5)
                zero_volume_days = 0
                for _, row in recent.iloc[::-1].iterrows():
                    if row.get("volume", 0) == 0:
                        zero_volume_days += 1
                    else:
                        break  # 遇到有成交量的日期就停止

                if zero_volume_days >= threshold_days:
                    alerts.append({
                        "code": code,
                        "type": "long_suspension",
                        "level": "CRITICAL",
                        "message": f"{code} 已停牌 {zero_volume_days} 天（超过 {threshold_days} 天阈值），建议标记风险",
                        "suspended_days": zero_volume_days,
                        "cost": pos.get("cost", 0),
                        "shares": pos.get("shares", 0),
                        "timestamp": datetime.now().isoformat(),
                    })

    return alerts


def format_suspension_alert(alerts: List[Dict]) -> str:
    """格式化停牌告警消息"""
    if not alerts:
        return ""

    lines = ["## 长期停牌风险告警", ""]
    for alert in alerts:
        lines.append(
            f"- **{alert['code']}**: 停牌 {alert['suspended_days']} 天 | "
            f"持仓 {alert['shares']}股 成本 {alert['cost']:.2f}"
        )
    lines.append("")
    lines.append(f"告警时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)


def push_suspension_alert(alerts: List[Dict]) -> bool:
    """推送停牌告警到飞书"""
    if not alerts:
        return False

    message = format_suspension_alert(alerts)
    try:
        from reporter.feishu_pusher import push_report
        return push_report(message, [])
    except Exception as e:
        logger.error(f"停牌告警推送失败: {e}")
        return False
