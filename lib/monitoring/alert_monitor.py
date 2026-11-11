"""异常告警监控 -- 检测异常并通过飞书推送

功能：
- 定期检查告警规则
- 支持静默期（免打扰时间段）
- 发送飞书/浏览器推送通知
"""

import json
import logging
import os
import time as _time
import uuid
from pathlib import Path
from typing import List, Dict
from datetime import datetime, time

import numpy as np  # P2 #6: 顶层 import 符合 PEP 8

from config import MonitorConfig, ALERT_FALLBACK_DIR as _CFG_ALERT_FALLBACK_DIR

logger = logging.getLogger(__name__)

# 默认静默期：22:00 - 08:00
DEFAULT_SILENT_START = time(22, 0)
DEFAULT_SILENT_END = time(8, 0)

# === Agent 1 Constants === (P2 #8: 提常量便于调参 + 测试)
# 数据缓存超过 24 小时视为过期（实盘每日 15:00 收盘后入库）
DEFAULT_STALE_HOURS = 24.0
# 北向资金 z-score 异常倍数（>2σ 视为异常流入/流出）
DEFAULT_NB_ZSCORE = 2.0
# 北向资金最小样本数（< 5 天数据不可靠）
# §4.2 A3 迁移 (2026-06-08): 原模块级常量 → config.MonitorConfig.min_nb_samples
MIN_NB_SAMPLES = MonitorConfig().min_nb_samples

# P7-4：降级目录单一真相源归 config.py（env 覆盖在 import 时生效）
FALLBACK_DIR = Path(_CFG_ALERT_FALLBACK_DIR)


class AlertMonitor:
    """异常告警监控器"""

    def __init__(self):
        self.alerts: List[Dict] = []
        self._silent_start = DEFAULT_SILENT_START
        self._silent_end = DEFAULT_SILENT_END
        # P1-1: 推送失败降级 — 计数器 + 降级文件
        self._push_failure_count: int = 0
        self._fallback_path: str = ""
        # §4.2 P1 #2 修复 (2026-06-08, lib-incremental-audit §3):
        # current_time 注入 (回测用), 默认 None = wall-clock 模式
        # run_all_checks 调用时设置, check_* 方法通过 _now_iso() 读
        self._current_time: Optional[time] = None

    def _now_iso(self) -> str:
        """§4.2 P1 #2: 返回当前时间 (time 对象) 或 wall-clock ISO 字符串

        优先级:
        1. self._current_time (time 对象, 仅时间戳, 默认 wall-clock)
        2. wall-clock (datetime.now().isoformat())
        """
        if self._current_time is not None:
            return f"TODAY-T{self._current_time.isoformat()}"
        return datetime.now().isoformat()

    def set_silent_period(self, start: time, end: time):
        """设置静默期"""
        self._silent_start = start
        self._silent_end = end

    def is_silent_period(self, current_time: time = None) -> bool:
        """检查当前是否在静默期

        Args:
            current_time: 注入的时间（用于 backtest）；None 时用 wall-clock
        """
        # P0-1: backtest 必须注入 current_time，否则 wall-clock 与回测日期错位
        now = current_time if current_time is not None else datetime.now().time()

        # 处理跨午夜的情况
        if self._silent_start <= self._silent_end:
            return self._silent_start <= now <= self._silent_end
        else:
            return now >= self._silent_start or now <= self._silent_end

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
                "timestamp": self._now_iso(),
            })
        return consecutive

    def check_drawdown(self, equity_curve: List[float], threshold: float = 0.03) -> bool:
        """检查**全期**回撤是否超过阈值（P2 #7: 修正文档与实现一致）

        实现：取 equity_curve 全局最大值为高水位 peak，current 为最后一点；
        算 (peak - current) / peak。文档原写"单日"是错误的——本函数实际
        是全期 high-water-mark 回撤（适合盘后/日内触发；日级单日跌幅请另写）。
        """
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
                "timestamp": self._now_iso(),
            })
            return True
        return False

    def check_data_staleness(self, cache_age_hours: float,
                             threshold_hours: float = DEFAULT_STALE_HOURS) -> bool:
        """检查数据是否过期"""
        if cache_age_hours > threshold_hours:
            self.alerts.append({
                "type": "data_stale",
                "level": "WARNING",
                "message": f"缓存数据已 {cache_age_hours:.0f} 小时未更新（阈值 {threshold_hours:.0f}h）",
                "timestamp": self._now_iso(),
            })
            return True
        return False

    def check_northbound_anomaly(self, daily_flows: list = None,
                                 threshold_multiplier: float = DEFAULT_NB_ZSCORE) -> bool:
        """检查北向资金异常流入/流出

        Args:
            daily_flows: 最近 N 天的净流入列表（单位：亿）
            threshold_multiplier: 标准差倍数阈值（默认 2 倍）

        Returns:
            True if anomaly detected
        """
        if not daily_flows or len(daily_flows) < MIN_NB_SAMPLES:
            return False

        # P2 #6: numpy 已移至模块顶层 import
        arr = np.array(daily_flows)
        mean = float(arr.mean())
        std = float(arr.std())

        if std == 0:
            return False

        latest = float(arr[-1])
        z_score = (latest - mean) / std

        if abs(z_score) > threshold_multiplier:
            direction = "大幅流入" if z_score > 0 else "大幅流出"
            self.alerts.append({
                "type": "northbound_anomaly",
                "level": "WARNING",
                "message": f"北向资金{direction}：今日 {latest:.1f}亿（均值 {mean:.1f}亿，标准差 {std:.1f}亿，Z={z_score:.1f}）",
                "timestamp": self._now_iso(),
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

        lines.append(f"告警时间：{self._now_iso()[:16].replace('T', ' ')}")
        return "\n".join(lines)

    def push_alerts(self) -> bool:
        """推送告警到飞书

        失败时降级：
        - 写本地 JSONL 降级文件（logs/alert_fallback/alert-{ts}-{id}.json）
        - 维护 _push_failure_count 计数器
        - 日志含 alert_id 便于人工定位
        """
        if not self.alerts:
            return False

        message = self.format_alert_message()
        # 生成 alert_id（每次推送唯一）
        alert_id = f"alert-{int(_time.time())}-{uuid.uuid4().hex[:8]}"
        try:
            from reporter.feishu_pusher import push_report
            success = push_report(message, [])
            if success:
                logger.info(f"告警推送成功 alert_id={alert_id}：{len(self.alerts)} 条")
            else:
                # 推送函数返回 False 也算失败，走降级
                self._push_failure_count += 1
                fallback = self._write_fallback(message, alert_id, reason="push_returned_false")
                logger.error(
                    f"告警推送失败 alert_id={alert_id}, count={self._push_failure_count}, "
                    f"已降级到 {fallback}"
                )
            return success
        except Exception as e:
            self._push_failure_count += 1
            fallback = self._write_fallback(message, alert_id, reason=f"exception:{e!r}")
            logger.error(
                f"告警推送异常 alert_id={alert_id}, count={self._push_failure_count}, "
                f"已降级到 {fallback}, error={e}"
            )
            return False

    def _write_fallback(self, message: str, alert_id: str, reason: str) -> str:
        """写入降级文件（JSONL 格式，含原始消息+元数据）

        Returns:
            str: 降级文件路径
        """
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = FALLBACK_DIR / f"{ts}-{alert_id}.json"
        self._fallback_path = str(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "alert_id": alert_id,
                    "timestamp": self._now_iso(),
                    "reason": reason,
                    "message": message,
                    "alerts": self.alerts,
                    "failure_count": self._push_failure_count,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        return str(path)

    def run_all_checks(self, trades: List[Dict] = None, equity_curve: List[float] = None,
                       cache_age_hours: float = 0,
                       current_time: time = None,
                       northbound_flows: list = None) -> List[Dict]:
        """运行所有检查（P2 #2: 文档与实现对齐 — 现在确实运行 4 项检查）

        §4.2 P1 #2 修复 (2026-06-08, lib-incremental-audit §3):
        - 旧 9 处 datetime.now() 时间戳 wall-clock, backtest 走实时日期
        - 修复: current_time 注入 (time 对象) → 全函数时间戳用 current_time
        - 不传 = 实时模式 (scanner 兼容), 传 = 回测

        Args:
            current_time: 注入时间（backtest 用）；None 时用 wall-clock
            northbound_flows: 最近 N 天北向资金净流入列表（亿）；None 跳过北向检查
        """
        # 检查静默期（支持时间注入）
        if self.is_silent_period(current_time=current_time):
            logger.info("In silent period, skipping alert checks")
            return []

        # §4.2 P1 #2 修复: 设当前时间 (time 对象), 让 _now_iso() 走注入
        self._current_time = current_time

        self.alerts = []

        if trades:
            self.check_consecutive_losses(trades)

        if equity_curve:
            self.check_drawdown(equity_curve)

        self.check_data_staleness(cache_age_hours)

        # P2 #2: 原实现漏调此方法 — 现在接受可选 northbound_flows，
        # 调用方（实盘/回测）注入即可触发北向异常告警
        if northbound_flows:
            self.check_northbound_anomaly(northbound_flows)

        return self.alerts
