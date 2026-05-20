"""基本面数据获取模块

多级缓存策略（线程安全）：
  1. 内存缓存 ← 5 分钟 TTL（热数据）
  2. 磁盘缓存 ← 24 小时 TTL（冷数据，跨 session 持久化）
  3. API 实时获取（akshare + baostock，失败时 fallback）

依赖：lib/cache.py
"""
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import logging
import threading
import time

import akshare as ak

from lib.cache import retry_api_call

logger = logging.getLogger(__name__)

# ========== 磁盘持久化缓存 ==========

CACHE_FILE = Path(__file__).resolve().parent.parent / "cache" / "fundamentals" / "fundamentals.parquet"

_financial_cache: Dict[str, tuple] = {}  # 内存缓存 {code: (pd.Series, timestamp)}
_financial_cache_lock = threading.Lock()
_disk_cache: Dict[str, dict] = {}  # 磁盘缓存镜像 {code: {field: value}}
_disk_cache_lock = threading.Lock()
_disk_loaded = False


def _load_disk_cache():
    """加载磁盘缓存到内存"""
    global _disk_cache, _disk_loaded
    with _disk_cache_lock:
        if _disk_loaded:
            return
    if CACHE_FILE.exists():
        try:
            df = pd.read_parquet(CACHE_FILE)
            cache_data = {}
            for _, row in df.iterrows():
                code = str(row.get("code", ""))
                if code:
                    rec = row.drop("code").to_dict()
                    rec = {k: (float(v) if not pd.isna(v) else None) for k, v in rec.items()}
                    cache_data[code] = rec
            with _disk_cache_lock:
                _disk_cache.update(cache_data)
                _disk_loaded = True
            logger.info(f"加载基本面磁盘缓存: {len(cache_data)} 只股票")
        except Exception as e:
            logger.debug(f"加载基本面缓存失败: {e}")
            with _disk_cache_lock:
                _disk_loaded = True


def _save_disk_cache():
    """保存内存缓存到磁盘"""
    with _disk_cache_lock:
        if not _disk_cache:
            return
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        records = []
        with _disk_cache_lock:
            for code, data in _disk_cache.items():
                rec = {"code": code}
                rec.update(data)
                records.append(rec)
        df = pd.DataFrame(records)
        df.to_parquet(CACHE_FILE, index=False)
    except Exception as e:
        logger.debug(f"保存基本面缓存失败: {e}")


def to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("亿", "").replace("万", ""))
    except (ValueError, TypeError):
        return None


def get_financial_data(code: str) -> Optional[pd.Series]:
    """获取个股基本面数据

    缓存策略（线程安全）：
      1. 内存缓存 ← 5 分钟 TTL（热数据）
      2. 磁盘缓存 ← 24 小时 TTL（冷数据，跨 session 持久化）
      3. API 实时获取（akshare + baostock，失败时 fallback）

    Returns: pd.Series with keys pe/pb/market_cap/revenue_growth/net_assets
    """
    now = time.time()
    _load_disk_cache()

    # 1. 检查内存缓存（5 分钟）
    with _financial_cache_lock:
        if code in _financial_cache:
            data, ts = _financial_cache[code]
            if now - ts < 300:
                return data

    # 2. 检查磁盘缓存（24 小时）
    if code in _disk_cache:
        disk_data = _disk_cache[code]
        age = now - disk_data.get("_ts", 0)
        if age < 86400:  # 24h
            series = pd.Series({
                "pe": disk_data.get("pe"),
                "pb": disk_data.get("pb"),
                "market_cap": disk_data.get("market_cap"),
                "revenue_growth": disk_data.get("revenue_growth"),
                "net_assets": disk_data.get("net_assets"),
                "pledge_ratio": None,
            })
            with _financial_cache_lock:
                _financial_cache[code] = (series, now)
            return series

    # 3. 实时获取
    try:
        info_df = retry_api_call(ak.stock_individual_info_em, symbol=code)
        if info_df is None or info_df.empty:
            with _financial_cache_lock:
                _financial_cache[code] = (None, now)
            return None

        info = {}
        for _, row in info_df.iterrows():
            info[row["item"]] = row["value"]

        data_dict = {
            "pe": to_float(info.get("市盈率-动态")),
            "pb": to_float(info.get("市净率")),
            "market_cap": to_float(info.get("总市值")),
            "revenue_growth": None,
            "net_assets": None,
        }

        # 尝试用 baostock 补充营收增长和净资产
        import baostock as bs
        try:
            lg = bs.login()
            if lg.error_code == "0":
                clean = code.replace("sh.", "").replace("sz.", "")
                bs_code = f"sh.{clean}" if clean.startswith("6") else f"sz.{clean}"
                year = datetime.now().year
                rs = bs.query_growth_data(bs_code, year=year, quarter=1)
                while (rs.error_code == "0") and rs.next():
                    rd = rs.get_row_data()
                    if len(rd) >= 6:
                        data_dict["revenue_growth"] = to_float(rd[4]) or to_float(rd[5])
                rs = bs.query_balance_data(bs_code, year=year, quarter=1)
                while (rs.error_code == "0") and rs.next():
                    rd = rs.get_row_data()
                    if len(rd) >= 6:
                        data_dict["net_assets"] = to_float(rd[5])
                bs.logout()
        except Exception as e:
            logger.warning(f"[fundamental_fetcher] baostock 营收/净资产查询失败: {e}")

        # 保存到磁盘缓存
        disk_entry = {k: v for k, v in data_dict.items()}
        disk_entry["_ts"] = now
        _disk_cache[code] = disk_entry
        _save_disk_cache()

        data = pd.Series(data_dict)
        with _financial_cache_lock:
            _financial_cache[code] = (data, now)
        return data

    except Exception as e:
        logger.debug(f"获取 {code} 基本面数据失败: {e}")
        with _financial_cache_lock:
            _financial_cache[code] = (None, now)
        return None
