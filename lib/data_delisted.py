"""退市股票数据源 —— 解决幸存者偏差

数据来源：
- AkShare: stock_info_a_delist() 获取退市股票列表
- AkShare: stock_zh_a_hist() 获取退市股票历史行情（部分可用）
- Baostock: query_history_basic_data() 退市股票历史数据

使用方式：
    from lib.data_delisted import get_delisted_symbols, get_delisted_stock_hist

注意：
- 退市股票的历史数据在退市后可能被部分平台移除
- AkShare 和 Baostock 是目前保留退市股数据较完整的开源方案
- 数据完整性不保证，建议与主数据源合并后标记来源
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 退市股票列表缓存
_delisted_cache: Optional[pd.DataFrame] = None
_delisted_cache_time: Optional[datetime] = None
CACHE_TTL_HOURS = 24


def get_delisted_symbols(force_refresh: bool = False) -> Set[str]:
    """获取所有退市股票代码集合

    Returns:
        Set[str]: 退市股票代码集合，如 {"000033", "600432", ...}
    """
    global _delisted_cache, _delisted_cache_time

    now = datetime.now()
    if (not force_refresh and _delisted_cache is not None
            and _delisted_cache_time is not None
            and (now - _delisted_cache_time).total_seconds() < CACHE_TTL_HOURS * 3600):
        if "代码" in _delisted_cache.columns:
            return set(_delisted_cache["代码"].astype(str).tolist())
        return set()

    codes = set()
    try:
        import akshare as ak
        # 分别获取沪市和深市退市股票
        for func_name in ["stock_info_sh_delist", "stock_info_sz_delist"]:
            try:
                df = getattr(ak, func_name)()
                if df is not None and not df.empty:
                    # 查找代码列（可能是 "代码" 或 "股票代码" 或 "证券代码"）
                    code_col = None
                    for col in df.columns:
                        if "代码" in str(col) or "code" in str(col).lower():
                            code_col = col
                            break
                    if code_col:
                        codes.update(df[code_col].astype(str).tolist())
            except Exception as e:
                logger.debug(f"{func_name} 获取失败: {e}")

        if codes:
            _delisted_cache = pd.DataFrame({"代码": list(codes)})
            _delisted_cache_time = now
            logger.info(f"获取退市股票列表: {len(codes)} 只")
            return codes
    except Exception as e:
        logger.warning(f"AkShare 退市列表获取失败: {e}")

    # Fallback: baostock
    try:
        import baostock as bs
        lg = bs.login()
        rs = bs.query_stock_basic()
        data = []
        while (rs.error_code == "0") and rs.next():
            row = rs.get_row_data()
            # row[4] = type, row[6] = status (0=退市)
            if len(row) > 6 and row[6] == "0":
                data.append(row[0])  # code
        bs.logout()
        if data:
            logger.info(f"Baostock 获取退市股票: {len(data)} 只")
            return set(data)
    except Exception as e:
        logger.warning(f"Baostock 退市列表获取失败: {e}")

    logger.warning("无法获取退市股票列表，回测将存在幸存者偏差")
    return set()


def get_delisted_stock_hist(symbol: str, days: int = 500) -> Optional[pd.DataFrame]:
    """获取退市股票历史行情（优先 TDX 本地数据，回退 AkShare/Baostock）

    Args:
        symbol: 股票代码（6位，如 "000033"）
        days: 获取天数

    Returns:
        DataFrame 或 None（数据不可用时）
    """
    from pathlib import Path

    # 1. 优先使用 TDX 本地数据（最可靠，无网络依赖）
    # bj 退市股加入字典（修复 bj 静默丢失）
    tdx_paths = {
        "sh": Path("D:/TDX/vipdoc/sh/lday_4/"),  # 前复权数据
        "sz": Path("D:/TDX/vipdoc/sz/lday_4/"),
        "bj": Path("D:/TDX/vipdoc/bj/lday/"),   # bj 无前复权，用 lday fallback
    }
    for market, base_path in tdx_paths.items():
        fpath = base_path / f"{market}{symbol}.day"
        if fpath.exists():
            try:
                df = _parse_tdx_day_file(fpath)
                if df is not None and len(df) > 30:
                    # 只取最近 N 天
                    if len(df) > days:
                        df = df.tail(days).reset_index(drop=True)
                    df["is_delisted"] = True
                    logger.debug(f"TDX 本地获取退市股 {symbol} 数据: {len(df)} 条")
                    return df
            except Exception as e:
                logger.debug(f"TDX 退市股 {symbol} 解析失败: {e}")

    # 2. 尝试 AkShare
    try:
        import akshare as ak
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq"
        )
        if df is not None and not df.empty:
            df = df.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume",
                "成交额": "amount", "涨跌幅": "pct_chg",
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            df["is_delisted"] = True
            logger.debug(f"AkShare 获取退市股 {symbol} 数据: {len(df)} 条")
            return df
    except Exception as e:
        logger.debug(f"AkShare 退市股 {symbol} 数据获取失败: {e}")

    return None


def _parse_tdx_day_file(filepath) -> Optional[pd.DataFrame]:
    """解析通达信 .day 文件

    TDX 日线文件格式（32字节/条）：
    - date (4 bytes, uint32): YYYYMMDD
    - open (4 bytes, uint32): 价格 × 100
    - high (4 bytes, uint32): 价格 × 100
    - low (4 bytes, uint32): 价格 × 100
    - close (4 bytes, uint32): 价格 × 100
    - amount (4 bytes, float32): 成交额
    - volume (4 bytes, uint32): 成交量
    - reserved (4 bytes): 保留
    """
    import struct

    try:
        data = []
        with open(filepath, "rb") as f:
            while True:
                buf = f.read(32)
                if len(buf) < 32:
                    break
                # 解析日期
                date_int = struct.unpack("<I", buf[0:4])[0]
                year = date_int // 10000
                month = (date_int % 10000) // 100
                day = date_int % 100
                if year < 1990 or year > 2030 or month < 1 or month > 12 or day < 1 or day > 31:
                    continue
                # 解析价格（除以100）
                open_p = struct.unpack("<I", buf[4:8])[0] / 100.0
                high_p = struct.unpack("<I", buf[8:12])[0] / 100.0
                low_p = struct.unpack("<I", buf[12:16])[0] / 100.0
                close_p = struct.unpack("<I", buf[16:20])[0] / 100.0
                amount = struct.unpack("<f", buf[20:24])[0]
                volume = struct.unpack("<I", buf[24:28])[0]

                if close_p <= 0:
                    continue

                data.append({
                    "date": pd.Timestamp(f"{year:04d}-{month:02d}-{day:02d}"),
                    "open": open_p, "high": high_p, "low": low_p, "close": close_p,
                    "volume": volume, "amount": amount,
                })

        if not data:
            return None

        df = pd.DataFrame(data)
        df = df.sort_values("date").reset_index(drop=True)
        return df

    except Exception as e:
        logger.debug(f"TDX 文件解析失败 {filepath}: {e}")
        return None


def get_all_delisted_hist(days: int = 500, max_stocks: int = 50) -> Dict[str, pd.DataFrame]:
    """批量获取退市股票历史数据

    Args:
        days: 获取天数
        max_stocks: 最大获取数量（控制 API 调用量）

    Returns:
        Dict[code -> DataFrame]
    """
    symbols = get_delisted_symbols()
    if not symbols:
        return {}

    # 只取最近退市的（更可能有数据）
    results = {}
    count = 0
    for symbol in list(symbols)[:max_stocks * 2]:  # 多尝试一些，因为部分可能无数据
        if count >= max_stocks:
            break
        df = get_delisted_stock_hist(symbol, days)
        if df is not None and len(df) > 30:
            results[symbol] = df
            count += 1

    logger.info(f"获取退市股票数据: {count}/{len(symbols)} 只有效")
    return results


def merge_with_delisted(
    live_data: Dict[str, pd.DataFrame],
    delisted_data: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """合并活股票和退市股票数据

    Args:
        live_data: 当前交易股票数据 {code: DataFrame}
        delisted_data: 退市股票数据 {code: DataFrame}

    Returns:
        合并后的数据字典，退市股票的 DataFrame 包含 is_delisted=True 标记
    """
    merged = live_data.copy()
    for code, df in delisted_data.items():
        if code not in merged:
            # 确保列名一致
            if "trade_date" not in df.columns and "date" in df.columns:
                df = df.rename(columns={"date": "trade_date"})
            merged[code] = df
    logger.info(f"合并数据: {len(live_data)} 活股票 + {len(delisted_data)} 退市股票 = {len(merged)} 总计")
    return merged
