"""推送心跳哨兵 —— 无人值守防线的失联检测（评审 P0-4，2026-11-20）。

判定真源 = ``feishu_pusher.PUSH_LOG_PATH`` 的末条**成功**推送时间（复核 B5：
废弃独立 heartbeat stamp 方案——重复真源 + 春节 26h 固定阈值误报），失联阈值按
**交易日**计数（``lib/data/trade_calendar``，日历缺失时退化为周末判断，不新增
误报源）。push_log 不存在 / 无成功条目 = 最强失联信号（从未推送或全失败）。

消费点：晨报入口（``scheduler.send_morning_report``）——每日固定触点，任何一次
运行都先自检上一段推送是否断流，失联则把告警段插入报告文本一起推送。P-79
（推送断流 6 个月才被发现）的结构性对策：把「没人看 push_log」变成「机器主动看」。
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 默认容忍窗口：末条成功推送距今超过 N 个交易日 → 失联。
# 2 = 容忍「昨夜失败今天补跑」类抖动；节假日按日历跳过（春节不再误报）。
DEFAULT_MAX_TRADING_DAYS = 2
# 交易日回数的硬上限（防日历缺失退化 + 机器钟错乱时的长循环）。
_TRADE_DAY_CAP = 30


def _local_date(ts_iso: str) -> Optional[date]:
    """push_log 的 UTC 时间戳 → 本地日期（时区归一，避免 UTC 深夜跨日误判）。"""
    try:
        return datetime.fromisoformat(ts_iso).astimezone().date()
    except (ValueError, TypeError, OSError):
        return None


def _load_last_success(push_log_path) -> tuple[Optional[date], str]:
    """读 push_log，返回 (末条成功推送的本地日期, 描述)；无成功条目返回 (None, 原因)。"""
    try:
        if not push_log_path.exists():
            return None, "push_log 不存在（从未有成功推送记录）"
        entries = json.loads(push_log_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 —— 日志损坏按最强失联信号处理
        return None, f"push_log 读取失败: {exc}"
    if not isinstance(entries, list) or not entries:
        return None, "push_log 为空"
    last: Optional[date] = None
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("success"):
            continue
        d = _local_date(str(entry.get("timestamp", "")))
        if d is not None and (last is None or d > last):
            last = d
    if last is None:
        return None, "push_log 无成功条目（近期推送全部失败）"
    return last, f"上次成功推送: {last.isoformat()}"


def trading_days_between(d_from: date, d_to: date, cap: int = _TRADE_DAY_CAP) -> int:
    """``(d_from, d_to]`` 区间内的交易日数（不含 d_from，含 d_to）。"""
    from lib.data.trade_calendar import is_trading_day

    n = 0
    cur = d_to
    while cur > d_from and n < cap:
        if is_trading_day(cur):
            n += 1
        cur -= timedelta(days=1)
    return n


def check_push_heartbeat(max_trading_days: int = DEFAULT_MAX_TRADING_DAYS,
                         today: Optional[date] = None) -> dict:
    """检查推送心跳是否失联。

    Returns:
        dict: ``stale`` / ``last_success_at`` / ``trading_days_since`` / ``reason``
        / ``max_trading_days``。``stale=True`` 时调用方应把告警并入下一次推送。
    """
    from reporter.feishu_pusher import PUSH_LOG_PATH

    today = today or date.today()
    last, reason = _load_last_success(PUSH_LOG_PATH)
    if last is None:
        logger.warning("[heartbeat] 推送心跳失联: %s", reason)
        return {
            "stale": True,
            "last_success_at": None,
            "trading_days_since": None,
            "reason": reason,
            "max_trading_days": max_trading_days,
        }

    if last > today:
        # 机器钟回拨/跨时区：按最新成功处理，不判失联（留痕即可）
        reason = f"{reason}（本地日期晚于 today，按未失联处理）"
        logger.warning("[heartbeat] %s", reason)
        return {
            "stale": False,
            "last_success_at": last.isoformat(),
            "trading_days_since": 0,
            "reason": reason,
            "max_trading_days": max_trading_days,
        }

    days = trading_days_between(last, today)
    stale = days > max_trading_days
    if stale:
        reason = f"{reason}，距今 {days} 个交易日 > 容忍 {max_trading_days}"
        logger.warning("[heartbeat] 推送心跳失联: %s", reason)
    return {
        "stale": stale,
        "last_success_at": last.isoformat(),
        "trading_days_since": days,
        "reason": reason,
        "max_trading_days": max_trading_days,
    }
