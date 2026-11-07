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
from typing import Optional

import pandas as pd

from lib.data.data_protocol import truncate_to_date

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

def _fetch_via_network(code: str, days: int, period: str, as_of_date: Optional[str] = None) -> pd.DataFrame:
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

        # as_of_date 截断（回测防前视偏差）：必须在 tail(days) **之前**
        df = truncate_to_date(df, as_of_date)

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
        except Exception as e:
            logger.debug(f"[tdx] 网络连接断开异常: {e}")


# ========== 本地文件获取 ==========

def _fetch_from_local_file(code: str, days: int, period: str, as_of_date: Optional[str] = None) -> pd.DataFrame:
    """读取本地 TDX .day 二进制文件"""
    # 北交所股票特殊处理（使用标准整数分格式）
    if code.startswith(("4", "8")):
        return _fetch_bj_local(code, days)

    try:
        from pytdx.reader import TdxDailyBarReader
    except ImportError:
        return pd.DataFrame()

    market, tdx_code = _to_tdx_market_code(code)
    market_str = "sh" if market == 1 else "sz"
    file_path = Path(TDX_DATA_PATH) / market_str / "lday" / f"{market_str}{tdx_code}.day"

    if not file_path.exists():
        return pd.DataFrame()

    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(file_path))
        if df.empty:
            return pd.DataFrame()

        # TdxDailyBarReader returns date as index, reset to column
        df = df.reset_index()

        # 统一列名
        col_map = {
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        }
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # as_of_date 截断（回测防前视偏差）：必须在 tail(days) **之前**
        df = truncate_to_date(df, as_of_date)

        # 只保留最近 days 天
        if len(df) > days:
            df = df.tail(days)

        return df[["date", "open", "high", "low", "close", "volume"]].copy()

    except Exception as e:
        logger.debug(f"TDX 本地文件读取 {code} 失败: {e}")
        return pd.DataFrame()


def _fetch_qfq_local_file(code: str, days: int, period: str, as_of_date: Optional[str] = None) -> pd.DataFrame:
    """读取本地 TDX 前复权 .day 二进制文件（sh/lday_4/, sz/lday_4/）

    Args:
        code: 6位股票代码
        days: 获取天数
        period: "daily" | "weekly" | "monthly"

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    # 北交所股票没有前复权数据，退回不复权版本
    if code.startswith(("4", "8")):
        return _fetch_from_local_file(code, days, period, as_of_date)

    try:
        from pytdx.reader import TdxDailyBarReader
    except ImportError:
        return pd.DataFrame()

    market, tdx_code = _to_tdx_market_code(code)
    market_str = "sh" if market == 1 else "sz"

    # 前复权文件在 lday_4 子目录
    file_path = Path(TDX_DATA_PATH) / market_str / "lday_4" / f"{market_str}{tdx_code}.day"

    if not file_path.exists():
        # 前复权文件不存在时，fallback 到不复权版本
        return _fetch_from_local_file(code, days, period, as_of_date)

    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(file_path))
        if df.empty:
            return _fetch_from_local_file(code, days, period, as_of_date)

        df = df.reset_index()

        col_map = {
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        }
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # as_of_date 截断（回测防前视偏差）：必须在 tail(days) **之前**
        df = truncate_to_date(df, as_of_date)

        if len(df) > days:
            df = df.tail(days)

        return df[["date", "open", "high", "low", "close", "volume"]].copy()

    except Exception as e:
        logger.debug(f"TDX 前复权文件读取 {code} 失败: {e}")
        # 前复权读取失败时 fallback 到不复权版本
        return _fetch_from_local_file(code, days, period, as_of_date)


# ========== 主接口 ==========

def get_kline(code: str, days: int = 180, period: str = "daily", as_of_date: Optional[str] = None) -> pd.DataFrame:
    """获取 K 线数据（兼容现有 data_*.py 接口）

    Args:
        code: 股票代码（如 "000001" 或 "600519"）
        days: 获取天数
        period: "daily" | "weekly" | "monthly"

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    # 优先网络直连
    df = _fetch_via_network(code, days, period, as_of_date)
    if not df.empty and len(df) >= 10:
        return df

    # 网络失败，尝试本地文件
    df = _fetch_from_local_file(code, days, period, as_of_date)
    if not df.empty:
        logger.debug(f"TDX 使用本地文件获取 {code}")
        return df

    # 本地文件也不存在，fallback 到 akshare
    logger.debug(f"TDX 网络+本地均失败，fallback 到 akshare 获取 {code}")
    try:
        import akshare as ak
        from lib.data.market_utils import detect_market
        market = detect_market(code)
        if market == "unknown":
            return pd.DataFrame()
        # akshare 不支持 bj 行情 → 跳过避免 silent wrong 数据
        if market == "bj":
            logger.debug(f"akshare 不支持 bj 股票 {code}，返回空")
            return pd.DataFrame()
        symbol = code if code.startswith(("sh", "sz")) else (market + code)
        df_ak = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        if df_ak is not None and not df_ak.empty:
            df_ak = df_ak.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume",
            })
            df_ak["date"] = pd.to_datetime(df_ak["date"])
            if len(df_ak) >= days:
                df_ak = df_ak.tail(days)
            return df_ak[["date", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        logger.debug(f"[tdx] akshare fallback 失败 [{code}]: {e}")

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
        except Exception as e:
            logger.debug(f"[tdx] 指数连接断开异常: {e}")


# ========== 本地文件：指数（sh/zs, sz/zs, tdx/zs）==========

def _fetch_index_local(index_code: str, days: int) -> pd.DataFrame:
    """读取本地 TDX 指数 .day 文件（标准格式：整数分）

    支持：上证指数(sh000001)、深证成指(sz399001)、通达信板块(sh880001)
    """
    code = index_code.strip().zfill(6)
    file_path = None

    # 规则：
    #   - 有 sh/sz 前缀：直接用
    #   - 无前缀：6开头→sh，0开头→sz（但 000001 特殊处理→sh）
    #   - 399开头→sz，8开头→tdx/zs（板块）
    def try_zs_path(market, c):
        p = Path(TDX_DATA_PATH) / market / "zs" / f"{market}{c}.day"
        if p.exists():
            return p
        return None

    def try_tdx_zs_path(market, c):
        p = Path(TDX_DATA_PATH) / "tdx" / "zs" / f"{market}{c}.day"
        if p.exists():
            return p
        return None

    if index_code.startswith("sh"):
        file_path = try_zs_path("sh", code)
        if file_path is None:
            file_path = try_tdx_zs_path("sh", code)
    elif index_code.startswith("sz") or code.startswith("399"):
        file_path = try_zs_path("sz", code)
    elif code.startswith("8"):
        # 板块在 tdx/zs，代码如 880001
        file_path = try_tdx_zs_path("sh", code)
    elif code.startswith(("6", "5", "9")):
        file_path = try_zs_path("sh", code)
    elif code.startswith(("0", "3", "2", "4")):
        # 000001 是上证指数，不是深证
        if code == "000001":
            file_path = try_zs_path("sh", code)
        else:
            file_path = try_zs_path("sz", code)
    else:
        file_path = None

    if file_path is None:
        return pd.DataFrame()

    # 直接用 struct 读取（比 TdxDailyBarReader 更可靠，兼容指数格式）
    return _read_standard_file(file_path, days, price_unit=0.01)


# ========== 本地文件：CSI 中证指数 ==========

def _fetch_csi_local(csi_code: str, days: int) -> pd.DataFrame:
    """读取中证指数本地 .day 文件（格式：8s + 4*f32）

    文件：csi/zs/62#000001CNY01.day
    """
    csi_code = csi_code.strip()
    file_path = Path(TDX_DATA_PATH) / "csi" / "zs"
    # 精确匹配
    for fpath in file_path.glob(f"62#{csi_code}.day"):
        return _parse_csi_file(fpath, days)
    # 带 CNY 后缀
    for fpath in file_path.glob(f"62#{csi_code}*.day"):
        return _parse_csi_file(fpath, days)
    return pd.DataFrame()


def _parse_csi_file(file_path: Path, days: int) -> pd.DataFrame:
    """解析 CSI 指数文件（每记录32字节：4字节日期 + 4*4字节OHLC + 12字节填充）

    格式：<date_i, open_f, high_f, low_f, close_f, padding_12bytes>
    """
    try:
        records = []
        with open(file_path, "rb") as f:
            while True:
                data = f.read(32)
                if len(data) < 32:
                    break
                date_val = struct.unpack('<i', data[0:4])[0]
                if not (19500101 <= date_val <= 20351231):
                    continue
                o, h, l, c = struct.unpack('<ffff', data[4:20])
                records.append({
                    "date": str(date_val),
                    "open": float(o), "high": float(h),
                    "low": float(l), "close": float(c),
                })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["date"])
        # CSI 数据 oldest-first
        if len(df) > days:
            df = df.tail(days)
        return df[["date", "open", "high", "low", "close"]].copy()
    except Exception as e:
        logger.debug(f"[tdx] CSI 指数文件解析失败: {e}")
        return pd.DataFrame()


# ========== 本地文件：港股通 ==========

def _fetch_ggt_local(code: str, days: int) -> pd.DataFrame:
    """读取港股通本地 .day 文件（标准整数分格式，单位 0.00001 HKD）

    文件：ggt/lday/31#00001.day
    """
    code = code.strip().zfill(5)
    file_path = Path(TDX_DATA_PATH) / "ggt" / "lday" / f"31#{code}.day"
    if not file_path.exists():
        return pd.DataFrame()
    return _read_standard_file(file_path, days, price_unit=0.00001)


# ========== 本地文件：北交所 ==========

def _fetch_bj_local(code: str, days: int) -> pd.DataFrame:
    """读取北交所本地 .day 文件（标准整数分格式，单位 0.0001 yuan）

    文件：bj/lday/bj810011.day
    """
    code = code.strip().zfill(6)
    file_path = Path(TDX_DATA_PATH) / "bj" / "lday" / f"bj{code}.day"
    if not file_path.exists():
        return pd.DataFrame()
    return _read_standard_file(file_path, days, price_unit=0.0001)


# ========== 本地文件：新三板 ==========

def _fetch_xsb_local(code: str, days: int) -> pd.DataFrame:
    """读取新三板本地 .day 文件（标准<IiiiiiII格式，价格单位 1e-8 yuan）

    文件：xsb/lday/44#400001.day
    amount 字段为原始整数（元），不经 price_unit 转换。
    """
    code = code.strip().zfill(6)
    file_path = Path(TDX_DATA_PATH) / "xsb" / "lday" / f"44#{code}.day"
    if not file_path.exists():
        return pd.DataFrame()
    try:
        records = []
        with open(file_path, "rb") as f:
            while True:
                data = f.read(32)
                if len(data) < 32:
                    break
                date_idx, o, h, l, c, amt, vol, _ = struct.unpack('<IiiiiiII', data)
                if not (19500101 <= date_idx <= 20351231):
                    break
                records.append({
                    "date": str(date_idx),
                    "open": o * 1e-8,
                    "high": h * 1e-8,
                    "low": l * 1e-8,
                    "close": c * 1e-8,
                    "volume": vol,
                    "amount": amt,  # amount 原始值（元），与 price_unit 无关
                })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["date"])
        if len(df) > days:
            df = df.tail(days)
        return df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    except Exception as e:
        logger.debug(f"[tdx] 新三板文件解析失败 [{code}]: {e}")
        return pd.DataFrame()


# ========== 本地文件：期权 ==========

def _fetch_option_local(code: str, market: str = "sh", days: int = 180) -> pd.DataFrame:
    """读取期权本地 .day 文件（标准整数分格式，价格单位 1e-8）

    文件：sh/option/8#10000001.day, sz/option/9#90000001.day
    amount 原始值（元），不经 price_unit 转换。
    """
    if market == "sh":
        file_path = Path(TDX_DATA_PATH) / "sh" / "option" / f"8#{code}.day"
    else:
        file_path = Path(TDX_DATA_PATH) / "sz" / "option" / f"9#{code}.day"
    if not file_path.exists():
        return pd.DataFrame()
    return _read_standard_file(file_path, days, price_unit=1e-8,
                               amount_is_raw=True)


# ========== 本地文件：基金 ==========

def _fetch_fund_local(code: str, market: str = "open", days: int = 180) -> pd.DataFrame:
    """读取基金本地 .day 文件（标准整数分格式，价格单位 1e-8 NAV）

    文件：fund/open/33#000001.day, fund/money/34#000009.day
    amount 原始值（元），不经 price_unit 转换。
    """
    if market == "money":
        file_path = Path(TDX_DATA_PATH) / "fund" / "money" / f"34#{code}.day"
    else:
        file_path = Path(TDX_DATA_PATH) / "fund" / "open" / f"33#{code}.day"
    if not file_path.exists():
        return pd.DataFrame()
    return _read_standard_file(file_path, days, price_unit=1e-8,
                               amount_is_raw=True)


# ========== 本地文件：宏观指标 ==========

def _fetch_macro_local(indicator: str, days: int = 30) -> pd.DataFrame:
    """读取宏观指标本地 .day 文件（<Iffffff 格式，28字节/记录）

    格式：<date_i, open_f, high_f, low_f, close_f, amount_f, volume_f>
    - GDP: 单位亿元（1e8 yuan）
    - CPI/PPI: 物价指数（基期=100）
    - SHIBOR/SLF/MLF: 利率（%）
    """
    # indicator 可能是完整文件名如 "38#1_GDP" 或简称 "GDP"
    macro_dir = Path(TDX_DATA_PATH) / "macro" / "lday"
    # 精确匹配
    for fpath in macro_dir.glob(f"*{indicator}*.day"):
        return _parse_macro_file(fpath, days)
    # 模糊匹配
    for fpath in macro_dir.glob(f"38#*.day"):
        if indicator.upper() in fpath.stem.upper():
            return _parse_macro_file(fpath, days)
    return pd.DataFrame()


def _parse_macro_file(file_path: Path, days: int) -> pd.DataFrame:
    """解析宏观指标文件（<Iffffff，28字节/记录）"""
    try:
        records = []
        with open(file_path, "rb") as f:
            while True:
                data = f.read(28)
                if len(data) < 28:
                    break
                date_val = struct.unpack('<i', data[0:4])[0]
                if not (19500101 <= date_val <= 20351231):
                    continue
                vals = struct.unpack('<ffffff', data[4:28])
                records.append({
                    "date": str(date_val),
                    "open": float(vals[0]),
                    "high": float(vals[1]),
                    "low": float(vals[2]),
                    "close": float(vals[3]),
                    "amount": float(vals[4]),
                    "volume": float(vals[5]),
                })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["date"])
        if len(df) > days:
            df = df.tail(days)
        return df[["date", "open", "high", "low", "close", "amount", "volume"]].copy()
    except Exception as e:
        logger.debug(f"[tdx] 宏观指标文件解析失败: {e}")
        return pd.DataFrame()


# ========== 本地文件：沪股通 ==========

def _fetch_hsj_local(code: str, market: str, days: int) -> pd.DataFrame:
    """读取沪股通本地 .day 文件（标准整数分格式，价格单位 0.01）

    文件：hsj/sh/lday/sh600000.day, hsj/sz/lday/sz000001.day, hsj/bj/lday/bj800010.day
    """
    code = code.strip().zfill(6)
    file_path = Path(TDX_DATA_PATH) / "hsj" / market / "lday" / f"{market}{code}.day"
    if not file_path.exists():
        return pd.DataFrame()
    return _read_standard_file(file_path, days, price_unit=0.01)


def get_hsj_local(code: str, market: str, days: int = 180) -> pd.DataFrame:
    """获取沪股通日线（本地文件）"""
    return _fetch_hsj_local(code, market, days)


# ========== 通用二进制文件读取 ==========

def _read_standard_file(file_path: Path, days: int, price_unit: float = 0.01,
                       amount_is_raw: bool = False) -> pd.DataFrame:
    """通用 TDX .day 文件读取（标准 <IiiiiiII 格式）

    Args:
        file_path: 文件路径
        days: 获取天数
        price_unit: 价格单位（0.01=元 for stocks, 1e-8=元 for XSB/option/fund）
        amount_is_raw: 若为 True，amount 不乘 price_unit，直接存原始值（元）
    """
    try:
        records = []
        with open(file_path, "rb") as f:
            while True:
                data = f.read(32)
                if len(data) < 32:
                    break
                date_idx, o, h, l, c, amt, vol, _ = struct.unpack('<IiiiiiII', data)
                if not (19500101 <= date_idx <= 20351231):
                    break
                records.append({
                    "date": str(date_idx),
                    "open": o * price_unit,
                    "high": h * price_unit,
                    "low": l * price_unit,
                    "close": c * price_unit,
                    "volume": vol,
                    "amount": float(amt) if amount_is_raw else amt * price_unit,
                })
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["date"])
        if len(df) > days:
            df = df.tail(days)
        return df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    except Exception as e:
        logger.debug(f"[tdx] 标准文件读取失败 [{file_path}]: {e}")
        return pd.DataFrame()


# ========== 主接口：多品种日线 ==========

def get_index_local(index_code: str, days: int = 180) -> pd.DataFrame:
    """获取指数日线（本地文件优先）

    Args:
        index_code: 指数代码，如 "000001"（上证）、"399001"（深证）、"000300"（沪深300）、"880001"（板块）
        days: 获取天数
    """
    # CSI 和 sh/zs/sz/zs 都尝试，返回数据最新者
    csi_df = _fetch_csi_local(index_code, days)
    zs_df = _fetch_index_local(index_code, days)

    # 选择数据更完整/最新的
    def latest_date(df):
        if df.empty or "date" not in df.columns:
            return None
        dates = pd.to_datetime(df["date"], errors="coerce").dropna()
        return dates.max() if len(dates) > 0 else None

    csi_latest = latest_date(csi_df)
    zs_latest = latest_date(zs_df)

    if csi_latest is not None and zs_latest is not None:
        # 选最新
        if csi_latest >= zs_latest:
            return csi_df
        return zs_df
    if csi_df is not None and not csi_df.empty:
        return csi_df
    if zs_df is not None and not zs_df.empty:
        return zs_df
    return pd.DataFrame()


def get_ggt_local(code: str, days: int = 180) -> pd.DataFrame:
    """获取港股通日线（本地文件）"""
    return _fetch_ggt_local(code, days)


def get_bj_local(code: str, days: int = 180) -> pd.DataFrame:
    """获取北交所日线（本地文件）"""
    return _fetch_bj_local(code, days)


def get_xsb_local(code: str, days: int = 180) -> pd.DataFrame:
    """获取新三板日线（本地文件）"""
    return _fetch_xsb_local(code, days)
