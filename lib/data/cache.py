"""缓存模块：API 限流、数据缓存（parquet/JSON）、重试机制"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import logging
import threading
import time

from config import CACHE_RATE_LIMIT
from lib.infra.utils import _safe_float

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


# ========== API 限流 ==========

class RateLimiter:
    """线程安全的 API 调用频率限制器"""
    def __init__(self, max_calls_per_second: float = CACHE_RATE_LIMIT):
        self._min_interval = 1.0 / max_calls_per_second
        self._last_call_time = 0.0
        self._lock = threading.Lock()

    def acquire(self):
        with self._lock:
            now = time.time()
            wait = self._last_call_time + self._min_interval - now
            self._last_call_time = max(now, self._last_call_time + self._min_interval)
        if wait > 0:
            time.sleep(wait)

_rate_limiter = RateLimiter(max_calls_per_second=CACHE_RATE_LIMIT)


def retry_api_call(func, *args, max_retries: int = 3, **kwargs):
    """带重试和限流的 API 调用"""
    last_error = None
    for attempt in range(max_retries):
        try:
            _rate_limiter.acquire()
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2.0 * (attempt + 1)
                logger.debug(f"API 调用失败 (尝试 {attempt + 1}/{max_retries})，{wait}s 后重试: {e}")
                time.sleep(wait)
    raise last_error


# 使用 lib.infra.utils 中的统一实现
safe_float = _safe_float


# ========== 数据缓存（带命中率统计）==========

_cache_stats = {"hits": 0, "misses": 0, "expired": 0, "errors": 0}
_stats_lock = threading.Lock()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.parquet"


def load_cache(key: str, max_age_hours: int = 2) -> pd.DataFrame | None:
    """加载缓存 DataFrame。

    返回值（必须区分的三种情况）：
    - DataFrame（非空）：加载成功，包含缓存数据
    - pd.DataFrame()（空）：缓存未命中（文件不存在 / 已过期），**预期行为，无需告警**
    - None：缓存加载失败（文件存在但读取失败：损坏/IO/权限），
            上游**必须**感知并走降级或告警路径

    错误处理（2026-06-07 P2-5 修复）：
    - 加载失败时升级到 logger.error（之前是 logger.debug，被静默吞掉）
    - 返回 None 作为显式错误标记（之前是返回空 df，调用方无法区分 miss vs error）
    - **不删除失败的文件**（保留现场，运维需人工诊断/手动删除）
      - 这与 data_layer._load_cache P1-2 修复一致：PermissionError/IO 错误保留文件

    命中率统计（2026-06-09 cherry-pick from wip/pipeline-refactor WIP）：
    - hits: 加载成功次数
    - misses: 文件不存在次数
    - expired: 文件过期被删除次数
    - errors: 加载失败次数
    """
    path = _cache_path(key)
    if not path.exists():
        # 缓存未命中，预期行为（debug 级别，避免日志噪音）
        logger.debug(f"[cache] 缓存未命中 [{key}]（文件不存在）")
        with _stats_lock:
            _cache_stats["misses"] += 1
        return pd.DataFrame()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=max_age_hours):
        # 缓存过期，删除并视为未命中
        path.unlink(missing_ok=True)
        logger.debug(f"[cache] 缓存过期 [{key}]（已删除）")
        with _stats_lock:
            _cache_stats["expired"] += 1
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
        with _stats_lock:
            _cache_stats["hits"] += 1
        return df
    except Exception as e:
        # P2-5 修复（2026-06-07）：从 logger.debug 升级到 logger.error
        # 失败时保留文件（不动 unlink），让运维诊断 PermissionError/IO 错误/损坏原因
        logger.error(
            f"[cache] 缓存加载失败 [{key}]: {type(e).__name__}: {e}（文件保留，路径: {path}）",
            exc_info=True,
        )
        with _stats_lock:
            _cache_stats["errors"] += 1
        return None  # 显式错误标记：None = 加载失败（与空 DataFrame 的 cache miss 区分）


def save_cache(key: str, df: pd.DataFrame):
    if not df.empty:
        df.to_parquet(_cache_path(key), index=False)


def get_cache_stats() -> dict:
    """返回缓存命中率统计（线程安全）

    返回字典包含 4 个计数器: hits / misses / expired / errors。
    调用方可用 hits / (hits + misses) 计算命中率。
    """
    with _stats_lock:
        return _cache_stats.copy()


def reset_cache_stats() -> None:
    """重置缓存命中率统计（线程安全）— 主要用于测试"""
    with _stats_lock:
        for k in _cache_stats:
            _cache_stats[k] = 0


# ========== 熔断器 ==========

class CircuitOpenError(Exception):
    """熔断器开启时抛出的异常"""
    pass


class CircuitBreaker:
    """熔断器 - API 连续失败 N 次后自动熔断，不再尝试

    支持命名断路器（name 参数），不同服务独立计数。
    不传 name 时使用默认断路器（向后兼容）。

    两种调用方式：
    - call(func, *args, **kwargs)         — 默认断路器
    - call(name, func, *args, **kwargs)   — 命名断路器（不同 name 独立计数）
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout  # 秒
        # 默认断路器状态
        self._failures = 0
        self._last_failure_time = 0
        self._state = "closed"  # closed / open / half_open
        # 命名断路器状态（per-service）
        self._named_states: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def _check_state(self, failures: int, last_ft: float, state: str) -> str:
        """检查并更新断路器状态，返回可能的新状态"""
        if state == "open":
            if time.time() - last_ft > self.recovery_timeout:
                return "half_open"
            raise CircuitOpenError(
                f"Circuit open, retry after {self.recovery_timeout}s"
            )
        return state

    def _record_success(self, name: str | None):
        with self._lock:
            if name and name in self._named_states:
                self._named_states[name]["failures"] = 0
                self._named_states[name]["state"] = "closed"
            else:
                self._failures = 0
                self._state = "closed"

    def _record_failure(self, name: str | None):
        with self._lock:
            if name and name in self._named_states:
                ns = self._named_states[name]
                ns["failures"] += 1
                ns["last_failure_time"] = time.time()
                if ns["failures"] >= self.failure_threshold:
                    ns["state"] = "open"
                    logger.warning(f"[断路器] {name} 已熔断（连续失败 {ns['failures']} 次）")
            else:
                self._failures += 1
                self._last_failure_time = time.time()
                if self._failures >= self.failure_threshold:
                    self._state = "open"

    def call(self, name_or_func, *args, **kwargs):
        """执行函数，失败时更新熔断状态。"""
        if callable(name_or_func):
            # 向后兼容：call(func, *args, **kwargs)
            func = name_or_func
            name = None
        else:
            # 命名断路器：call(name, func, *args, **kwargs)
            name = name_or_func
            func = args[0]
            args = args[1:]

        # 初始化命名断路器
        if name is not None:
            with self._lock:
                if name not in self._named_states:
                    self._named_states[name] = {
                        "failures": 0, "last_failure_time": 0, "state": "closed",
                    }

        # 检查断路状态
        with self._lock:
            if name and name in self._named_states:
                ns = self._named_states[name]
                ns["state"] = self._check_state(
                    ns["failures"], ns["last_failure_time"], ns["state"]
                )
            else:
                self._state = self._check_state(
                    self._failures, self._last_failure_time, self._state
                )

        # 执行函数（锁外，允许并发）
        try:
            result = func(*args, **kwargs)
            self._record_success(name)
            return result
        except Exception as e:
            self._record_failure(name)
            raise

    def record_failure(self, name: str | None = None) -> None:
        """手动记一次失败（公开 API）。

        ADR-0019（2026-11-14）：挂起类故障（不抛错、不返回）无法经 call() 的异常
        路径计数——调用方在超时裁剪后用本方法把挂起计入熔断，使「慢挂起」与
        「快失败」同权重地驱动熔断开启。
        """
        self._record_failure(name)

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        """重置为关闭状态（清空默认 + 全部命名断路器状态；用于测试/运维）。

        2026-11-14 修复：data_source_registry.reset_circuit_breakers 此前调用
        不存在的 `_reset()`——该方法从未存在，reset 是一调即 AttributeError 的
        死代码（无调用方，潜伏至本批才被发现）。
        """
        with self._lock:
            self._failures = 0
            self._last_failure_time = 0
            self._state = "closed"
            self._named_states.clear()


# ========== 缓存清理 ==========

def clean_cache(max_age_hours: int = 24, max_total_mb: int = 500):
    """清理过期缓存 + LRU 淘汰"""
    if not CACHE_DIR.exists():
        return 0
    now = datetime.now()
    deleted = 0
    total_size = 0
    alive_files = []
    for path in CACHE_DIR.iterdir():
        if not path.is_file():
            continue
        if path.suffix not in (".parquet", ".json"):
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            size = path.stat().st_size
            age_hours = (now - mtime).total_seconds() / 3600
            if age_hours > max_age_hours:
                path.unlink(missing_ok=True)
                deleted += 1
            else:
                total_size += size
                alive_files.append((path, mtime, size))
        except Exception as e:
            logger.debug(f"[cache] 缓存文件处理失败: {e}")
            continue
    max_bytes = max_total_mb * 1024 * 1024
    if total_size > max_bytes:
        alive_files.sort(key=lambda x: x[1])
        for path, _, size in alive_files:
            path.unlink(missing_ok=True)
            total_size -= size
            deleted += 1
            if total_size <= max_bytes:
                break
    if deleted > 0:
        logger.info(f"缓存清理: 删除 {deleted} 个文件")
    return deleted
