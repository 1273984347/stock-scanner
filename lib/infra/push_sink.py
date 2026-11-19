"""lib 层推送出口 —— 依赖倒置网关（评审 P1-6，2026-11-20）。

此前 lib 三处（pipeline IC 告警 / paper_trading_runner 日报 / alert_monitor 告警）
lazy import reporter.feishu_pusher——低层直接依赖顶层推送实现（V9 倒挂）。

倒置后：lib 只调 ``push_text(text)``；**reporter 在被 import 时自注册 sink**
（feishu_pusher 模块底部 ``set_push_sink(...)``）。三个扫描入口（run.py /
api.py / scripts/daily_scan.py）显式 ``import reporter.feishu_pusher`` 保证
sink 就位。sink 未注册 = warning + False（可见降级，不再静默）。

sink 签名：``fn(text: str) -> bool``（单通道文本推送；图表等富内容仍在
reporter 层自行处理）。
"""
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_sink: Optional[Callable[[str], bool]] = None


def set_push_sink(fn: Callable[[str], bool]) -> None:
    """注册推送 sink（reporter 层调用；重复注册以最后一次为准）。"""
    global _sink
    _sink = fn


def push_text(text: str, *, title: str = "") -> bool:
    """lib 层统一推送出口：返回 True = sink 确认送达。"""
    fn = _sink
    if fn is None:
        logger.warning(
            "[push-sink] 未注册（reporter 未被加载），降级 log-only：%s %s",
            title, (text or "")[:60].replace("\n", " "))
        return False
    try:
        return bool(fn(text))
    except Exception as e:  # noqa: BLE001 —— 推送失败不得炸业务链路
        logger.error("[push-sink] sink 异常: %s", e)
        return False
