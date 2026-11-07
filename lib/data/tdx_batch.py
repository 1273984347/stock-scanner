"""TDX 批量读取优化 —— 并行读取 + 缓存

替代逐文件读取模式，支持：
1. 并行读取多只股票（ThreadPoolExecutor）
2. 内存缓存（LRU，避免重复读取）
3. mmap 优化（大文件零拷贝）

使用方式：
    from lib.data.tdx_batch import batch_read_closes, batch_read_day_files
"""

import struct
import mmap
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import numpy as np
import threading

from lib.data.market_utils import get_tdx_path

logger = logging.getLogger(__name__)

# 统一从 config 读取（单一真相源），不再硬编码
TDX_DATA_PATH = Path(os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc"))

# 全局缓存
_closes_cache: Dict[str, np.ndarray] = {}
_cache_lock = threading.Lock()
_CACHE_MAX_SIZE = 5000


def _read_day_file_raw(fpath: Path, days: int = 60) -> Optional[List[Dict]]:
    """用 mmap 读取单个 TDX 日线文件（零拷贝）"""
    if not fpath.exists():
        return None
    try:
        with open(fpath, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                file_size = mm.size()
                n = file_size // 32
                if n < max(days, 10):
                    return None

                start_offset = max(0, (n - days) * 32)
                records = []
                for offset in range(start_offset, file_size, 32):
                    if offset + 32 > file_size:
                        break
                    date_int, o, h, l, c, amount, vol, _ = struct.unpack_from("<IiiiiiII", mm, offset)
                    records.append({
                        "date": str(date_int),
                        "open": o / 100.0, "high": h / 100.0, "low": l / 100.0, "close": c / 100.0,
                        "amount": amount, "volume": vol,
                    })
                return records
    except Exception:
        return None


def _read_closes_only(fpath: Path, days: int = 60) -> Optional[np.ndarray]:
    """用 mmap 只读取收盘价（零拷贝，最快路径）"""
    if not fpath.exists():
        return None
    try:
        with open(fpath, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                file_size = mm.size()
                n = file_size // 32
                if n < max(days, 10):
                    return None

                start_offset = max(0, (n - days) * 32)
                count = (file_size - start_offset) // 32
                closes = np.zeros(count)
                for i in range(count):
                    closes[i] = struct.unpack_from("<i", mm, start_offset + i * 32 + 16)[0] / 100.0
                return closes
    except Exception:
        return None


def batch_read_closes(codes: List[str], days: int = 60, max_workers: int = 8) -> Dict[str, np.ndarray]:
    """批量读取收盘价（并行+缓存）

    Args:
        codes: 股票代码列表
        days: 读取天数
        max_workers: 并行线程数

    Returns:
        dict: {code: np.array of closes}
    """
    result = {}
    to_fetch = []

    # 先查缓存
    with _cache_lock:
        for code in codes:
            cache_key = f"{code}_{days}"
            if cache_key in _closes_cache:
                result[code] = _closes_cache[cache_key]
            else:
                to_fetch.append(code)

    if not to_fetch:
        return result

    # 并行读取
    def _fetch_one(code: str) -> Tuple[str, Optional[np.ndarray]]:
        # 用 helper 替代手写市场判断 + lday_4 硬编码（修复 bj 静默丢失）
        fpath = get_tdx_path(code, prefer_qfq=True)
        if fpath is None:
            return code, None
        closes = _read_closes_only(fpath, days)
        return code, closes

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, code): code for code in to_fetch}
        for future in as_completed(futures):
            try:
                code, closes = future.result()
            except Exception as e:
                logger.debug(f"TDX batch read_closes failed for {futures[future]}: {e}")
                continue
            if closes is not None:
                result[code] = closes
                # 更新缓存
                with _cache_lock:
                    if len(_closes_cache) < _CACHE_MAX_SIZE:
                        _closes_cache[f"{code}_{days}"] = closes

    return result


def batch_read_day_files(codes: List[str], days: int = 60, max_workers: int = 8) -> Dict[str, List[Dict]]:
    """批量读取完整日线数据（并行）

    Args:
        codes: 股票代码列表
        days: 读取天数
        max_workers: 并行线程数

    Returns:
        dict: {code: list of day records}
    """
    result = {}

    def _fetch_one(code: str) -> Tuple[str, Optional[List[Dict]]]:
        fpath = get_tdx_path(code, prefer_qfq=True)
        if fpath is None:
            return code, None
        records = _read_day_file_raw(fpath, days)
        return code, records

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, code): code for code in codes}
        for future in as_completed(futures):
            try:
                code, records = future.result()
            except Exception as e:
                logger.debug(f"TDX batch read_day_files failed for {futures[future]}: {e}")
                continue
            if records is not None:
                result[code] = records

    return result


def batch_read_with_indicators(codes: List[str], days: int = 60, max_workers: int = 8) -> Dict[str, Dict]:
    """批量读取并计算基础指标（EMA/RSI/ATR）

    Returns:
        dict: {code: {closes, ema21, rsi14, atr14}}
    """
    raw_data = batch_read_day_files(codes, days, max_workers)
    result = {}

    for code, records in raw_data.items():
        if len(records) < 30:
            continue

        closes = np.array([r["close"] for r in records])
        highs = np.array([r["high"] for r in records])
        lows = np.array([r["low"] for r in records])

        # EMA21
        ema21 = _ema(closes, 21)

        # RSI14
        rsi14 = _rsi(closes, 14)

        # ATR14
        atr14 = _atr(highs, lows, closes, 14)

        result[code] = {
            "closes": closes,
            "ema21": ema21,
            "rsi14": rsi14,
            "atr14": atr14,
            "last_close": closes[-1],
            "last_ema21": ema21[-1] if len(ema21) > 0 else 0,
            "last_rsi14": rsi14[-1] if len(rsi14) > 0 else 50,
            "last_atr14": atr14[-1] if len(atr14) > 0 else 0,
        }

    return result


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """EMA 计算（numpy 版）"""
    alpha = 2.0 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI 计算（numpy 版）"""
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.zeros(len(closes))
    avg_loss = np.zeros(len(closes))

    if len(gains) < period:
        return np.full(len(closes), 50.0)

    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])

    for i in range(period + 1, len(closes)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gains[i-1]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + losses[i-1]) / period

    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    rsi = 100 - 100 / (1 + rs)
    return rsi


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """ATR 计算（numpy 版）"""
    prev_close = np.roll(closes, 1)
    prev_close[0] = closes[0]

    tr = np.maximum(
        highs - lows,
        np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close))
    )

    atr = np.zeros_like(tr)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period

    return atr


def clear_cache():
    """清除缓存"""
    with _cache_lock:
        _closes_cache.clear()
