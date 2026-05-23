"""缓存模块：API 限流、数据缓存（parquet/JSON）、重试机制"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import logging
import threading
import time

from config import CACHE_RATE_LIMIT
from lib.utils import _safe_float

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
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


# 使用 lib.utils 中的统一实现
safe_float = _safe_float


# ========== 数据缓存 ==========

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.parquet"


def load_cache(key: str, max_age_hours: int = 2) -> pd.DataFrame:
    path = _cache_path(key)
    if not path.exists():
        return pd.DataFrame()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=max_age_hours):
        path.unlink(missing_ok=True)
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        path.unlink(missing_ok=True)
        return pd.DataFrame()


def save_cache(key: str, df: pd.DataFrame):
    if not df.empty:
        df.to_parquet(_cache_path(key), index=False)


# ========== 熔断器 ==========

class CircuitOpenError(Exception):
    """熔断器开启时抛出的异常"""
    pass


class CircuitBreaker:
    """熔断器 - API 连续失败 N 次后自动熔断，不再尝试"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout  # 秒
        self._failures = 0
        self._last_failure_time = 0
        self._state = "closed"  # closed / open / half_open
        self._lock = threading.Lock()

    def call(self, func, *args, **kwargs):
        """执行函数，失败时更新熔断状态"""
        with self._lock:
            if self._state == "open":
                if time.time() - self._last_failure_time > self.recovery_timeout:
                    self._state = "half_open"
                else:
                    raise CircuitOpenError(
                        f"Circuit open, retry after {self.recovery_timeout}s"
                    )

        try:
            result = func(*args, **kwargs)
            self._reset()
            return result
        except Exception as e:
            self._record_failure()
            raise

    def _record_failure(self):
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()
            if self._failures >= self.failure_threshold:
                self._state = "open"

    def _reset(self):
        with self._lock:
            self._failures = 0
            self._state = "closed"

    @property
    def state(self) -> str:
        return self._state


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
        except Exception:
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
