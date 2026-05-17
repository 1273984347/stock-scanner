"""TDX（通达信）数据源 —— 双模式：网络直连优先，文件兜底

安装：pip install pytdx
配置：
  TDX_DATA_PATH - 本地 TDX 文件目录（默认 D:/TDX/vipdoc）
  TDX_HOST      - pytdx 服务器地址（默认 180.153.39.210）
  TDX_PORT      - pytdx 端口（默认 7709）

数据来源：
  1. 网络直连：通过 pytdx hq API 获取（优先）
  2. 本地文件：读取 TDX binary .day 文件（兜底）
"""
import os
import struct
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ========== 配置 ==========
TDX_DATA_PATH = os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc")
TDX_HOST = os.environ.get("TDX_HOST", "180.153.39.210")
TDX_PORT = int(os.environ.get("TDX_PORT", "7709"))

# ========== 代码转换 ==========

def _to_tdx_market_code(code: str):
    """转换为 (market, code) 元组

    market: 0=深圳, 1=上海
    code: 6位字符串
    """
    code = code.strip().zfill(6)
    if code.startswith(("6", "5", "9")):
        return 1, code  # 上海
    elif code.startswith(("0", "3", "2")):
        return 0, code  # 深圳
    elif code.startswith(("4", "8")):
        return 0, code  # 北交所归深圳
    else:
        return 0, code  # 默认深圳


def _to_tdx_code(code: str) -> str:
    """转换为 6 位 TDX 代码格式"""
    return code.strip().zfill(6)


# ========== 网络获取 ==========

def _fetch_via_network(code: str, days: int, period: str) -> pd.DataFrame:
    """通过 pytdx hq API 获取 K 线"""
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return pd.DataFrame()

    api = TdxHq_API(heartbeat=True, auto_retry=True)
    try:
        api.connect(TDX_HOST, TDX_PORT, timeouts=5)
        market, tdx_code = _to_tdx_market_code(code)

        # period -> TDX category
        cat_map = {"daily": 4, "weekly": 5, "monthly": 6}
        category = cat_map.get(period, 4)

        # 获取足够多的数据
        count = min(days + 50, 800)
        data = api.get_security_bars(category, market, tdx_code, 0, count)

        if data is None or len(data) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        if "date" in df.columns:
            df = df.rename(columns={"date": "date"})
            df["date"] = pd.to_datetime(df["date"], errors="coerce")

        # 统一列名
        col_map = {
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume", "amount": "amount",
        }
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # 只保留最近 days 天
        if len(df) > days:
            df = df.tail(days)

        return df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()

    except Exception as e:
        logger.debug(f"TDX 网络获取 {code} 失败: {e}")
        return pd.DataFrame()
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


# ========== 本地文件获取 ==========

def _fetch_from_local_file(code: str, days: int, period: str) -> pd.DataFrame:
    """读取本地 TDX .day 二进制文件"""
    try:
        from pytdx.reader import TdxDailyBarReader
    except ImportError:
        return pd.DataFrame()

    market, tdx_code = _to_tdx_market_code(code)
    market_str = "sh" if market == 1 else "sz"
    file_path = Path(TDX_DATA_PATH) / market_str / "lday" / f"{market_str}{tdx_code}.day"

    if not file_path.exists():
        # 尝试北交所路径
        file_path = Path(TDX_DATA_PATH) / "bj" / "lday" / f"bj{tdx_code}.day"
        if not file_path.exists():
            return pd.DataFrame()

    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(file_path))
        if df.empty:
            return pd.DataFrame()

        # 统一列名
        col_map = {
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        }
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # 只保留最近 days 天
        if len(df) > days:
            df = df.tail(days)

        return df[["date", "open", "high", "low", "close", "volume"]].copy()

    except Exception as e:
        logger.debug(f"TDX 本地文件读取 {code} 失败: {e}")
        return pd.DataFrame()


# ========== 主接口 ==========

def get_kline(code: str, days: int = 180, period: str = "daily") -> pd.DataFrame:
    """获取 K 线数据（兼容现有 data_*.py 接口）

    Args:
        code: 股票代码（如 "000001" 或 "600519"）
        days: 获取天数
        period: "daily" | "weekly" | "monthly"

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    # 优先网络直连
    df = _fetch_via_network(code, days, period)
    if not df.empty and len(df) >= 10:
        return df

    # 网络失败，尝试本地文件
    df = _fetch_from_local_file(code, days, period)
    if not df.empty and len(df) >= 10:
        logger.debug(f"TDX 使用本地文件获取 {code}")
        return df

    return pd.DataFrame()


# ========== 指数日线 ==========

def get_index_daily(index_code: str, days: int = 180) -> pd.DataFrame:
    """获取指数日线（通过 pytdx 网络）"""
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return pd.DataFrame()

    api = TdxHq_API(heartbeat=True, auto_retry=True)
    try:
        api.connect(TDX_HOST, TDX_PORT, timeouts=5)
        market, tdx_code = _to_tdx_market_code(index_code)

        count = min(days + 50, 800)
        data = api.get_index_bars(4, market, tdx_code, 0, count)

        if data is None or len(data) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        if "date" in df.columns:
            df = df.rename(columns={"date": "date"})
            df["date"] = pd.to_datetime(df["date"], errors="coerce")

        col_map = {
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume", "amount": "amount",
        }
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        if len(df) > days:
            df = df.tail(days)

        return df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()

    except Exception as e:
        logger.debug(f"TDX 指数获取 {index_code} 失败: {e}")
        return pd.DataFrame()
    finally:
        try:
            api.disconnect()
        except Exception:
            pass
