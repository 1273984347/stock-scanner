"""数据源 host 级健康度（ADR-0025，2026-11-18）。

背景（坑表 P-99 同源）：
    2026-11-18 实测发现东财并非「整个源挂了」，而是**按 host 部分不可用**：
    ``push2.eastmoney.com``（全市场行情/资金流）空响应、
    ``push2his.eastmoney.com``（K 线）连接重置，
    而 ``push2ex.eastmoney.com``（涨停池/龙虎榜）正常返回（41 行 / 188 行）。

    旧熔断体系只有「按数据源（baostock/akshare/…）」一维计数，没有 host 维度：
    要么整个源被标记不可用、连带把正常的 push2ex 一起停掉；要么完全不计数、
    每次调用都先付 8s 超时 + 3s 限流（单股 ~11s）再降级。

本模块给出第二维：**按 host 独立计数 + 冷却后半开探测**。
判据语义：
    - 连续 3 次失败 → 该 host 开断 300s；期间 ``is_available()`` 返回 False，
      调用方必须在**发请求前**短路（省掉超时与限流等待）。
    - 冷却到期 → 半开：放行**一次**探测；成功闭合，失败立即重开并重置计时。
    - host 之间完全独立：push2 开断不影响 push2ex（有机器判据钉死）。
"""
import functools
import logging
import threading
import time
from typing import Callable, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 逻辑名 → host（写成常量供调用方 import，避免各处硬编码字符串拼错）
HOST_EASTMONEY_PUSH2 = "push2.eastmoney.com"        # 行情快照 / 资金流 K 线
HOST_EASTMONEY_PUSH2HIS = "push2his.eastmoney.com"  # 历史 K 线
HOST_EASTMONEY_PUSH2EX = "push2ex.eastmoney.com"    # 涨停池 / 龙虎榜（实测可用）
HOST_EASTMONEY_EMWEB = "emweb.securities.eastmoney.com"  # F10 财务
HOST_TENCENT_QUOTE = "qt.gtimg.cn"
HOST_TENCENT_KLINE = "ifzq.gtimg.cn"
HOST_TENCENT_KLINE_WEB = "web.ifzq.gtimg.cn"
HOST_SINA_QUOTE = "hq.sinajs.cn"
HOST_SINA_MARKET = "vip.stock.finance.sina.com.cn"
HOST_SINA_KLINE = "money.finance.sina.com.cn"

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_S = 300.0


class HostHealth:
    """按 host 的健康状态机（closed / open / half_open）。"""

    def __init__(self, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
                 cooldown_s: float = DEFAULT_COOLDOWN_S,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._states: Dict[str, dict] = {}

    def _state(self, host: str) -> dict:
        return self._states.setdefault(host, {
            "state": "closed", "failures": 0,
            "opened_at": 0.0, "last_error": "", "last_change": self._clock(),
        })

    def is_available(self, host: str) -> bool:
        """是否允许向该 host 发请求（冷却到期后放行一次半开探测）。"""
        with self._lock:
            st = self._state(host)
            if st["state"] == "closed":
                return True
            if st["state"] == "open" and self._clock() - st["opened_at"] >= self.cooldown_s:
                st["state"] = "half_open"
                st["last_change"] = self._clock()
                logger.info(f"[host_health] {host} 冷却到期 → 半开探测")
                return True
            return st["state"] == "half_open"

    def note_success(self, host: str) -> None:
        with self._lock:
            st = self._state(host)
            if st["state"] != "closed" or st["failures"]:
                logger.info(f"[host_health] {host} 恢复正常，闭合熔断")
            st.update(state="closed", failures=0, opened_at=0.0,
                      last_error="", last_change=self._clock())

    def note_failure(self, host: str, error: Optional[BaseException] = None) -> None:
        with self._lock:
            st = self._state(host)
            st["failures"] += 1
            st["last_error"] = str(error)[:200] if error else ""
            st["last_change"] = self._clock()
            # 半开探测失败 → 立即重开（不等再攒到阈值）
            if st["state"] == "half_open" or st["failures"] >= self.failure_threshold:
                if st["state"] != "open":
                    logger.warning(
                        f"[host_health] {host} 连续失败 {st['failures']} 次 → 开断 "
                        f"{self.cooldown_s:.0f}s（last_error={st['last_error']}）")
                st["state"] = "open"
                st["opened_at"] = self._clock()

    def snapshot(self) -> Dict[str, dict]:
        """当前各 host 状态（运维/端点用，不含锁对象）。"""
        with self._lock:
            return {h: dict(st) for h, st in self._states.items()}

    def reset(self) -> None:
        with self._lock:
            self._states.clear()


# 进程级单例（与 data_layer 的进程内缓存同寿命；跨进程不共享——
# 每个 worker 自行探测，避免把本机网络状态伪装成全局事实）
_HOST_HEALTH = HostHealth()


def is_available(host: str) -> bool:
    return _HOST_HEALTH.is_available(host)


def note_success(host: str) -> None:
    _HOST_HEALTH.note_success(host)


def note_failure(host: str, error: Optional[BaseException] = None) -> None:
    _HOST_HEALTH.note_failure(host, error)


def health_snapshot() -> Dict[str, dict]:
    return _HOST_HEALTH.snapshot()


def reset_host_health() -> None:
    _HOST_HEALTH.reset()


# ========== 调用包装：把 host 健康度接进取数链路 ==========

class HostUnavailableError(Exception):
    """host 已开断（保留给需要显式区分「没发请求」的调用方）。"""


def _call(host: str, fn: Callable, args: tuple, kwargs: dict):
    """开断期直接返回空帧；异常记失败并**原样抛出**；成功闭合。

    为什么异常要原样抛出：既有调用方（`_retry_api_call` / 多源循环）靠异常
    触发重试与切源；在这里吞掉会静默降低重试能力。开断期返回空帧则是
    「源返回无数据」的等价语义，既有兜底链照常工作，且不再付超时成本。
    """
    if not is_available(host):
        logger.debug(f"[host_health] {host} 开断中，跳过 {getattr(fn, '__name__', fn)}")
        return pd.DataFrame()
    try:
        result = fn(*args, **kwargs)
    except Exception as e:
        note_failure(host, e)
        logger.warning(
            f"[host_health] {host} 调用失败 {getattr(fn, '__name__', fn)}: {e}")
        raise
    note_success(host)
    return result


def call_with_health(host: str, fn: Callable, *args, **kwargs):
    """直接调用（用于不需要重试包装的取数点）。"""
    return _call(host, fn, args, kwargs)


def guarded(host: str, fn: Callable) -> Callable:
    """返回带 host 健康度的包装函数；``functools.wraps`` 保留 ``__module__``，
    以维持 `rate_limiter` 按模块分桶的既有配额语义（不能退化成单桶）。"""
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        return _call(host, fn, args, kwargs)
    return _wrapped
