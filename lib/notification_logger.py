"""通知历史记录器 —— 归档所有通知"""

import json
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

NOTIFICATION_DIR = Path("cache/notifications")


@dataclass
class NotificationRecord:
    """通知记录"""
    timestamp: str
    type: str  # signal / trade / alert / system
    title: str
    body: str
    level: str = "info"  # info / warning / critical
    read: bool = False


class NotificationLogger:
    """通知日志管理器"""

    def __init__(self):
        NOTIFICATION_DIR.mkdir(parents=True, exist_ok=True)
        # §4.2 P1 #7 修复 (2026-06-08, lib-incremental-audit §2.5):
        # 旧实现文件 append 无锁 — 多线程并发写 jsonl 可能丢行 / 截断
        # 修复: 进程级 Lock (NotificationLogger 是单例, 锁覆盖所有方法)
        self._lock = threading.Lock()

    def log(self, notification: NotificationRecord):
        """记录一条通知"""
        date_str = datetime.now().strftime("%Y%m%d")
        log_file = NOTIFICATION_DIR / f"notifications_{date_str}.jsonl"

        # §4.2 P1 #7 修复: 写 jsonl 持锁
        with self._lock:
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(notification), ensure_ascii=False) + "\n")
            except Exception as e:
                logger.debug(f"通知日志写入失败: {e}")

    def log_signal(self, code: str, signal: str, confidence: float):
        """记录信号通知"""
        self.log(NotificationRecord(
            timestamp=datetime.now().isoformat(),
            type="signal",
            title=f"新信号: {code}",
            body=f"{signal} 置信度 {confidence:.2f}",
            level="info",
        ))

    def log_alert(self, alert_type: str, message: str, level: str = "warning"):
        """记录告警通知"""
        self.log(NotificationRecord(
            timestamp=datetime.now().isoformat(),
            type="alert",
            title=alert_type,
            body=message,
            level=level,
        ))

    def log_trade(self, code: str, action: str, pnl_pct: float = 0):
        """记录交易通知"""
        self.log(NotificationRecord(
            timestamp=datetime.now().isoformat(),
            type="trade",
            title=f"{action}: {code}",
            body=f"盈亏 {pnl_pct:.2%}" if pnl_pct != 0 else action,
            level="info" if pnl_pct >= 0 else "warning",
        ))

    def get_recent(self, days: int = 7, type_filter: str = None) -> List[Dict]:
        """获取最近通知"""
        notifications = []
        cutoff = datetime.now() - timedelta(days=days)

        for f in sorted(NOTIFICATION_DIR.glob("notifications_*.jsonl"), reverse=True):
            try:
                date_str = f.stem.replace("notifications_", "")
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date < cutoff:
                    break
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            record = json.loads(line)
                            if type_filter and record.get("type") != type_filter:
                                continue
                            notifications.append(record)
            except Exception:
                continue

        return notifications[-100:]  # 最近 100 条
