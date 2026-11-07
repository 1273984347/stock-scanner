"""数据源注册与熔断管理模块

从 data_layer.py 提取的熔断逻辑，负责：
- 多数据源优先级管理
- 每个数据源的独立熔断器
- 数据源自动切换
"""
import pandas as pd
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

from lib.cache import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# ========== 数据源配置 ==========

DATA_SOURCE_PRIORITY = ["tdx_local_qfq", "tdx_local", "baostock", "tdx", "adata", "westock", "tushare", "akshare"]
# 回测用：前复权数据源优先（tdx_local_qfq 是前复权本地文件，优先于 baostock 网络）
DATA_SOURCE_PRIORITY_ADJUSTED = ["tdx_local_qfq", "baostock", "akshare", "tdx_local", "tdx", "adata", "westock", "tushare"]


def _is_bj_stock(code: str) -> bool:
    """判断是否为北交所股票（4/8 开头：43/83/87/88/92）"""
    return code.startswith(("4", "8"))


# 每个数据源独立的熔断器
_CIRCUIT_BREAKERS: Dict[str, CircuitBreaker] = {
    src: CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    for src in DATA_SOURCE_PRIORITY
}


def _fetch_from_source(code: str, days: int, source: str, as_of_date: Optional[str] = None) -> pd.DataFrame:
    """从指定数据源获取数据

    P0-10 修复 (2026-06-08, lib-incremental-audit §2.1):
    - 旧实现 tushare/akshare 用 datetime.now() 拉**今日**实时数据
    - 回测环境若不传 as_of_date，会用 T 日行情 = 前视偏差
    - 修复: as_of_date 透传到 tushare/akshare 端点 (其他数据源行为待补)
    - 不传 as_of_date 时仍走 datetime.now()（实时模式，行为不变）

    Args:
        code: 股票代码
        days: 历史天数
        source: 数据源名称
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。

    Returns:
        DataFrame with columns: date/open/high/low/close/volume
    """
    breaker = _CIRCUIT_BREAKERS.get(source)
    if breaker is None:
        logger.warning(f"未知数据源: {source}")
        return pd.DataFrame()

    # P0-10: 透传 as_of_date 端点
    if as_of_date is not None:
        end_dt = pd.to_datetime(as_of_date)
    else:
        end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    end_str = end_dt.strftime("%Y%m%d")
    start_str = start_dt.strftime("%Y%m%d")

    try:
        if source == "tdx_local_qfq":
            from lib.data_tdx import _fetch_qfq_local_file
            return _fetch_qfq_local_file(code, days, "daily", as_of_date)
        if source == "tdx_local":
            from lib.data_tdx import _fetch_from_local_file
            return _fetch_from_local_file(code, days, "daily", as_of_date)
        if source == "baostock":
            from lib.data_baostock import get_kline as baostock_kline
            return breaker.call(baostock_kline, code, days=days, as_of_date=as_of_date)
        if source == "tdx":
            from lib.data_tdx import get_kline as tdx_kline
            return breaker.call(tdx_kline, code, days=days, as_of_date=as_of_date)
        if source == "adata":
            from lib.data_adata import get_kline as adata_kline
            return breaker.call(adata_kline, code, days=days, as_of_date=as_of_date)
        if source == "westock":
            from lib.data_westock import get_kline as westock_kline
            df = breaker.call(westock_kline, code, period="day", limit=min(days, 2000),
                             as_of_date=as_of_date)
            if df.empty:
                return pd.DataFrame()
            col_map = {"日期": "date", "开盘": "open", "收盘": "close",
                       "最高": "high", "最低": "low", "成交量": "volume"}
            df = df.rename(columns=col_map)
            for c in ["open", "close", "high", "low", "volume"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            return df
        if source == "tushare":
            from lib.data_tushare import get_daily, _to_ts_code
            ts_code = _to_ts_code(code)
            # P0-10: 透传 as_of_date (旧用 datetime.now() 前视)
            df = breaker.call(get_daily, ts_code, start_str, end_str)
            if df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                "trade_date": "date", "open": "open", "close": "close",
                "high": "high", "low": "low", "vol": "volume"
            })
            return df
        if source == "akshare":
            import akshare as ak
            # P0-10: 透传 as_of_date (旧用 datetime.now() 前视)
            df = breaker.call(ak.stock_zh_a_hist, symbol=code, period="daily",
                              start_date=start_str, end_date=end_str, adjust="qfq")
            if df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume",
                "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg"
            })
            return df
    except CircuitOpenError:
        logger.warning(f"[{source}] 熔断器开启，跳过该数据源")
        return pd.DataFrame()
    except Exception as e:
        logger.debug(f"[{source}] 数据源调用失败: {e}")
        return pd.DataFrame()

    logger.warning(f"未知数据源: {source}")
    return pd.DataFrame()


# 列名标准化映射表
COLUMN_MAPPINGS = {
    # 日期列
    "trade_date": "date",
    "tradeDate": "date",
    "日期": "date",
    # OHLCV 核心列
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "vol": "volume",
    # 扩展列
    "成交额": "amount",
    "turnover": "amount",
    "换手率": "turnover",
    "turnoverRate": "turnover",
    "涨跌幅": "pct_chg",
    "changePct": "pct_chg",
    "pctChg": "pct_chg",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名为标准列名"""
    if df.empty:
        return df

    # 1. 应用所有映射
    rename_map = {}
    for orig, standard in COLUMN_MAPPINGS.items():
        if orig in df.columns and standard not in df.columns:
            rename_map[orig] = standard
    if rename_map:
        df = df.rename(columns=rename_map)

    # 2. 日期格式统一
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    # 3. 数值列类型统一
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _validate_dataframe(df: pd.DataFrame, min_rows: int = 10, required_cols: list = None) -> bool:
    """数据完整性校验"""
    if df.empty or len(df) < min_rows:
        return False
    required_cols = required_cols or ["date", "open", "high", "low", "close", "volume"]
    return all(c in df.columns for c in required_cols)


class AllDataSourcesFailedError(Exception):
    """所有数据源均无法获取数据时抛出"""
    def __init__(self, code: str, errors: list):
        self.code = code
        self.errors = errors
        super().__init__(f"所有数据源均无法获取 {code}: {errors}")


def get_stock_hist_multi_source(
    code: str,
    days: int = 180,
    source: str = "auto",
    prefer_adjusted: bool = False,
    as_of_date: Optional[str] = None,
) -> pd.DataFrame:
    """多数据源自动切换获取个股日线

    统一返回列名: date/open/high/low/close/vol
    所有数据源均失败时抛出 AllDataSourcesFailedError

    P0-10 修复 (2026-06-08): 新增 as_of_date 透传到各数据源 (防回测前视)

    Args:
        code: 股票代码
        days: 历史天数
        source: 数据源，"auto" 表示自动切换
        prefer_adjusted: 是否优先使用前复权数据（回测时应设为 True）
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传, 实时可省略。
            不传时走 datetime.now() (旧行为)

    Returns:
        DataFrame with columns: date/open/high/low/close/volume
    """
    if source != "auto":
        df = _fetch_from_source(code, days, source, as_of_date=as_of_date)
        df = _normalize_columns(df)
        if not _validate_dataframe(df):
            return pd.DataFrame()
        return df

    # 北交所股票：强制使用 tdx_local（其他数据源不支持）
    if _is_bj_stock(code):
        df = _fetch_from_source(code, days, "tdx_local", as_of_date=as_of_date)
        if not df.empty:
            df = _normalize_columns(df)
            if _validate_dataframe(df):
                logger.debug(f"使用数据源 [tdx_local] 获取北交所 {code}")
                return df
        raise AllDataSourcesFailedError(code, ["北交所股票仅支持本地TDX数据源"])

    errors = []
    # 选择数据源优先级：prefer_adjusted=True 时前复权数据源优先
    priority = DATA_SOURCE_PRIORITY_ADJUSTED if prefer_adjusted else DATA_SOURCE_PRIORITY
    for src in priority:
        df = _fetch_from_source(code, days, src, as_of_date=as_of_date)
        if not df.empty:
            df = _normalize_columns(df)
            if _validate_dataframe(df):
                if src != "akshare":
                    logger.debug(f"使用数据源 [{src}] 获取 {code}")
                return df
        errors.append(f"{src}: empty/invalid")
    raise AllDataSourcesFailedError(code, errors)


def get_circuit_breaker_status() -> Dict[str, dict]:
    """获取所有数据源熔断器状态"""
    return {
        src: {
            "failure_count": cb._failures,
            "last_failure_time": cb._last_failure_time,
            "state": cb.state,
        }
        for src, cb in _CIRCUIT_BREAKERS.items()
    }


def reset_circuit_breakers():
    """重置所有熔断器（用于测试）"""
    for cb in _CIRCUIT_BREAKERS.values():
        cb._reset()
