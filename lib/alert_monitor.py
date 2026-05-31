"""异常告警监控 -- 检测异常并通过飞书推送"""

import logging
from typing import List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class AlertMonitor:
    """异常告警监控器"""

    def __init__(self):
        self.alerts: List[Dict] = []

    def check_consecutive_losses(self, trades: List[Dict], threshold: int = 5) -> bool:
        """检查连续亏损是否超过阈值"""
        if len(trades) < threshold:
            return False

        recent = trades[-threshold:]
        consecutive = all(t.get("pnl_pct", 0) < 0 for t in recent)

        if consecutive:
            self.alerts.append({
                "type": "consecutive_losses",
                "level": "WARNING",
                "message": f"连续 {threshold} 笔亏损！最近 {threshold} 笔交易全部亏损",
                "details": [f"{t.get('code', '?')}: {t.get('pnl_pct', 0):.2%}" for t in recent],
                "timestamp": datetime.now().isoformat(),
            })
        return consecutive

    def check_drawdown(self, equity_curve: List[float], threshold: float = 0.03) -> bool:
        """检查单日回撤是否超过阈值"""
        if len(equity_curve) < 2:
            return False

        peak = max(equity_curve)
        current = equity_curve[-1]
        drawdown = (peak - current) / peak if peak > 0 else 0

        if drawdown > threshold:
            self.alerts.append({
                "type": "drawdown",
                "level": "CRITICAL",
                "message": f"回撤 {drawdown:.1%} 超过阈值 {threshold:.1%}",
                "timestamp": datetime.now().isoformat(),
            })
            return True
        return False

    def check_data_staleness(self, cache_age_hours: float, threshold_hours: float = 24) -> bool:
        """检查数据是否过期"""
        if cache_age_hours > threshold_hours:
            self.alerts.append({
                "type": "data_stale",
                "level": "WARNING",
                "message": f"缓存数据已 {cache_age_hours:.0f} 小时未更新（阈值 {threshold_hours:.0f}h）",
                "timestamp": datetime.now().isoformat(),
            })
            return True
        return False

    def format_alert_message(self) -> str:
        """格式化告警消息"""
        if not self.alerts:
            return ""

        lines = ["## 系统告警", ""]
        for alert in self.alerts:
            level_emoji = "🔴" if alert["level"] == "CRITICAL" else "🟡"
            lines.append(f"{level_emoji} **{alert['type']}**: {alert['message']}")
            if "details" in alert:
                for detail in alert["details"][:5]:
                    lines.append(f"  - {detail}")
            lines.append("")

        lines.append(f"告警时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)

    def push_alerts(self) -> bool:
        """推送告警到飞书"""
        if not self.alerts:
            return False

        message = self.format_alert_message()
        try:
            from reporter.feishu_pusher import push_report
            success = push_report(message, [])
            if success:
                logger.info(f"告警推送成功：{len(self.alerts)} 条")
            else:
                logger.warning("告警推送失败")
            return success
        except Exception as e:
            logger.error(f"告警推送异常: {e}")
            return False

    def run_all_checks(self, trades: List[Dict] = None, equity_curve: List[float] = None,
                       cache_age_hours: float = 0) -> List[Dict]:
        """运行所有检查"""
        self.alerts = []

        if trades:
            self.check_consecutive_losses(trades)

        if equity_curve:
            self.check_drawdown(equity_curve)

        self.check_data_staleness(cache_age_hours)

        return self.alerts
