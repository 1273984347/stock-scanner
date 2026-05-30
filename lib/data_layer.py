"""数据层：多源数据获取、缓存、限流"""
import os
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import get_config
from lib.cache import CircuitBreaker, CircuitOpenError
from lib.broker import LRUCache
from lib.utils import _safe_float
from lib.data_source_registry import (
    _normalize_columns, _validate_dataframe, AllDataSourcesFailedError,
    _fetch_from_source, DATA_SOURCE_PRIORITY, _CIRCUIT_BREAKERS,
)

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# ========== API 限流 ==========

class _RateLimiter:
    """线程安全的 API 调用频率限制器"""
    def __init__(self, max_calls_per_second: float = 3.0):
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

_rate_limiter = _RateLimiter(max_calls_per_second=3.0)


def _retry_api_call(func, *args, max_retries: int = 3, **kwargs):
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


# ========== 数据缓存 ==========

FIN_CACHE_DIR = CACHE_DIR / "fin"
FIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(key: str) -> Path:
    """支持多层级子目录的缓存路径，如 fin/600519 或 hsj/sh/600000/180

    安全：禁止包含 .. 或绝对路径，防止路径遍历攻击
    """
    # 防止路径遍历：禁止 .. 或绝对路径分量
    if ".." in key or key.startswith("/") or key.startswith("~"):
        raise ValueError(f"Invalid cache key: {key}")
    if "/" in key:
        parts = key.split("/")
        subdir = parts[0]
        # 再次检查 subdir 不包含 ..
        if ".." in subdir or subdir.startswith("/"):
            raise ValueError(f"Invalid cache key subdir: {subdir}")
        (CACHE_DIR / subdir).mkdir(parents=True, exist_ok=True)
        return CACHE_DIR / (key.replace("/", "_") + ".parquet")
    return CACHE_DIR / f"{key}.parquet"


def _load_cache(key: str, max_age_hours: int = 2) -> pd.DataFrame:
    path = _cache_path(key)
    if not path.exists():
        return pd.DataFrame()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=max_age_hours):
        path.unlink(missing_ok=True)
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.debug(f"[data_layer] 缓存读取失败 [{key}]: {e}")
        path.unlink(missing_ok=True)
        return pd.DataFrame()


def _save_cache(key: str, df: pd.DataFrame):
    if not df.empty:
        df.to_parquet(_cache_path(key), index=False)


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
            logger.debug(f"[data_layer] 缓存文件处理失败: {e}")
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


# ========== 基本面数据获取 ==========

from lib.data_cache import get_financial_cache

_financial_cache = get_financial_cache()  # 统一缓存实例


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("亿", "").replace("万", ""))
    except (ValueError, TypeError):
        return None


def _get_financial_data(code: str) -> Optional[pd.Series]:
    """获取个股基本面数据（PE/PB/市值/质押率等）

    数据源优先级：
    1. FinancialCache（统一缓存，内存5min + 磁盘7天）
    2. 腾讯财经（PE/PB/市值）—— 直连，不依赖第三方
    3. akshare（PE/PB/市值）—— 备用
    4. 东方财富财报（毛利率/经营现金流/负债率/增长率）

    缓存：5分钟内存LRU + 7天 parquet 持久化（通过 FinancialCache 统一管理）
    """
    # 1. 缓存检查（优先复用 get_stock_fundamentals 的结果）
    cached = _financial_cache.get(code)
    if cached is not None:
        # 从统一 dict 格式转换为 _get_financial_data 约定的 pd.Series
        prof = cached.get("profitability") or {}
        solv = cached.get("solvency") or {}
        growth = cached.get("growth") or {}
        return pd.Series({
            "pe": None, "pb": None, "market_cap": None,
            "gross_margin": prof.get("gross_margin"),
            "revenue_growth": growth.get("revenue_growth"),
            "net_income_growth": growth.get("net_income_growth"),
            "operating_cash_flow": None,
            "_roe": prof.get("ROE"),
            "_debt_to_asset": solv.get("debt_to_asset"),
            "_source": cached.get("_source"),
        })

    # 2. 腾讯 PE/PB/市值（直连，不走缓存）
    data = pd.Series({
        "pe": None, "pb": None, "market_cap": None,
        "pledge_ratio": None, "net_assets": None,
        "revenue_growth": None, "prev_revenue_growth": None,
        "gross_margin": None, "operating_cash_flow": None,
    })

    try:
        from lib.data_ashare import get_tencent_pe_pb
        tencent = get_tencent_pe_pb(code)
        if tencent.get("pe"):
            data["pe"] = tencent["pe"]
        if tencent.get("pb"):
            data["pb"] = tencent["pb"]
        if tencent.get("market_cap"):
            data["market_cap"] = tencent["market_cap"]
    except Exception as e:
        logger.debug(f"[data_layer] 腾讯 PE/PB 获取失败 {code}: {e}")

    # 3. akshare 兜底（PE/PB/市值）
    try:
        info_df = _retry_api_call(ak.stock_individual_info_em, symbol=code)
        if info_df is not None and not info_df.empty and "item" in info_df.columns and "value" in info_df.columns:
            info = dict(zip(info_df["item"].astype(str), info_df["value"]))
            if data.get("pe") is None:
                data["pe"] = _to_float(info.get("市盈率-动态"))
            if data.get("pb") is None:
                data["pb"] = _to_float(info.get("市净率"))
            if data.get("market_cap") is None:
                data["market_cap"] = _to_float(info.get("总市值"))
    except Exception as e:
        logger.debug(f"[data_layer] akshare 基本面获取失败 {code}: {e}")

    # 4. 东方财富财报（毛利率 + 经营现金流 + 负债率 + 增长率）
    try:
        from lib.data_eastmoney_fin import get_em_financial
        em_data = get_em_financial(code)
        if em_data:
            if data.get("gross_margin") is None and em_data.get("gross_margin") is not None:
                data["gross_margin"] = em_data["gross_margin"]
            ocf = em_data.get("ocf_per_share")
            if ocf is not None:
                data["operating_cash_flow"] = ocf  # 每股经营现金流（元），用于 ocf_healthy 判断
            if em_data.get("revenue_growth") is not None:
                data["revenue_growth"] = em_data["revenue_growth"]
    except Exception as e:
        logger.debug(f"[data_layer] 东方财富财报获取失败 {code}: {e}")

    return data


# ========== 日线数据获取 ==========

def _fetch_from_tdx_local(code: str, days: int) -> pd.DataFrame:
    """从本地 TDX .day 文件读取日线（前复权优先，不复权兜底，取最新）

    前复权文件：sh/lday_4/, sz/lday_4/
    不复权文件：sh/lday/, sz/lday/

    当前复权数据过期时，自动 fallback 到不复权数据（取更新更晚的那个）。
    """
    try:
        from pytdx.reader import TdxDailyBarReader
    except ImportError:
        return pd.DataFrame()

    code = code.strip().zfill(6)
    if code.startswith(("6", "5", "9")):
        market_str = "sh"
    elif code.startswith(("0", "3", "2", "4", "8")):
        market_str = "sz"
    else:
        return pd.DataFrame()

    tdx_path = os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc")
    reader = TdxDailyBarReader()

    def _read_tdx_file(file_path: str) -> pd.DataFrame:
        """读取单个 .day 文件并标准化"""
        df = reader.get_df(file_path)
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        col_map = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df[["date", "open", "high", "low", "close", "volume"]].copy()

    # 读取两个数据源
    qfq_path = Path(tdx_path) / market_str / "lday_4" / f"{market_str}{code}.day"
    hfq_path = Path(tdx_path) / market_str / "lday" / f"{market_str}{code}.day"

    df_qfq = pd.DataFrame()
    df_hfq = pd.DataFrame()

    if qfq_path.exists():
        try:
            df_qfq = _read_tdx_file(str(qfq_path))
        except Exception as e:
            logger.debug(f"[data_layer] TDX 前复权文件读取失败 [{code}]: {e}")

    if hfq_path.exists():
        try:
            df_hfq = _read_tdx_file(str(hfq_path))
        except Exception as e:
            logger.debug(f"[data_layer] TDX 不复权文件读取失败 [{code}]: {e}")

    # 选择更新更晚的数据源（前复权优先，但不复权更新更快时 fallback）
    if df_qfq.empty and df_hfq.empty:
        return pd.DataFrame()

    if df_qfq.empty:
        df = df_hfq
    elif df_hfq.empty:
        df = df_qfq
    else:
        qfq_date = df_qfq["date"].max()
        hfq_date = df_hfq["date"].max()
        # 前复权数据足够新（>= 不复权）时优先使用前复权
        if qfq_date >= hfq_date:
            df = df_qfq
        else:
            # 不复权数据更新，fallback
            logger.info(f"[data_layer] TDX 前复权过期 [{code}]: qfq={qfq_date} < hfq={hfq_date}，使用不复权数据")
            df = df_hfq

    if len(df) > days:
        df = df.tail(days)
    return df


def get_stock_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取个股日线：本地TDX优先，网络akshare兜底（带缓存）"""
    cache_key = f"hist/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached

    # 1. 优先本地 TDX 文件
    df = _fetch_from_tdx_local(code, days)
    if not df.empty and len(df) >= min(days, 5):
        _save_cache(cache_key, df)
        return df

    # 2. 网络兜底
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = _retry_api_call(ak.stock_zh_a_hist, symbol=code, period="daily",
                             start_date=start, end_date=end, adjust="qfq")
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg"
        })
        _save_cache(cache_key, df)
        return df
    except Exception as e:
        logger.error(f"获取 {code} 数据失败: {e}")
        return pd.DataFrame()


# 数据源熔断逻辑已移至 lib/data_source_registry.py
from lib.data_source_registry import (
    get_stock_hist_multi_source,
    get_circuit_breaker_status,
    DATA_SOURCE_PRIORITY,
)


# ========== 指数/板块日线（本地 TDX 优先）==========

def get_index_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取指数/板块日线（本地 TDX 优先，网络兜底）

    Args:
        code: 指数代码，如 "000001"（上证）、"399001"（深证）、"880001"（板块）
        days: 获取天数
    """
    from lib.data_tdx import get_index_local
    cache_key = f"index/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    # 本地优先
    df = get_index_local(code, days)
    if not df.empty:
        _save_cache(cache_key, df)
        return df
    # 网络兜底
    try:
        from lib.data_tdx import get_index_daily as _get_index_daily_net
        df = _get_index_daily_net(code, days)
        if not df.empty:
            _save_cache(cache_key, df)
        return df
    except Exception as e:
        logger.debug(f"[data_layer] 指数日线获取失败 [{code}]: {e}")
        return pd.DataFrame()


def get_ggt_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取港股通日线（本地文件）"""
    from lib.data_tdx import get_ggt_local
    cache_key = f"ggt/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = get_ggt_local(code, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


# ========== 沪股通日线（hsj）==========

def get_hsj_hist(code: str, market: str = "sh", days: int = 180) -> pd.DataFrame:
    """获取沪股通日线（本地文件）"""
    from lib.data_tdx import get_hsj_local
    cache_key = f"hsj/{market}/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = get_hsj_local(code, market, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_option_hist(code: str, market: str = "sh", days: int = 180) -> pd.DataFrame:
    """获取期权日线（本地文件）"""
    from lib.data_tdx import _fetch_option_local
    cache_key = f"option/{market}/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = _fetch_option_local(code, market, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_fund_hist(code: str, market: str = "open", days: int = 180) -> pd.DataFrame:
    """获取基金净值日线（本地文件）"""
    from lib.data_tdx import _fetch_fund_local
    cache_key = f"fund/{market}/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = _fetch_fund_local(code, market, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_macro_hist_local(indicator: str = "CPI", days: int = 30) -> pd.DataFrame:
    """获取宏观指标历史（本地 TDX 文件）

    Args:
        indicator: 指标名，如 "CPI", "GDP", "SHIBOR_1W", "SLF7D"
        days: 最大记录数（宏观数据通常为月度/季度，优先返回最新数据）
    """
    from lib.data_tdx import _fetch_macro_local
    cache_key = f"macro/{indicator}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.macro_max_age_hours)
    if not cached.empty:
        return cached
    df = _fetch_macro_local(indicator, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_bj_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取北交所日线（本地文件）"""
    from lib.data_tdx import get_bj_local
    cache_key = f"bj/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = get_bj_local(code, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


# ========== 新三板日线（xsb）==========

def get_xsb_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取新三板日线（本地文件）

    注意：新三板流动性较差，仅作数据完整性保留。

    Args:
        code: 股票代码（6位，如 "400001"）
        days: 获取天数
    """
    from lib.data_tdx import get_xsb_local
    cache_key = f"xsb/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = get_xsb_local(code, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


# ========== 实时行情 ==========

def get_realtime_quotes() -> pd.DataFrame:
    cached = _load_cache("realtime/realtime", max_age_hours=get_config().cache.realtime_max_age_minutes / 60)
    if not cached.empty:
        return cached
    try:
        df = _retry_api_call(ak.stock_zh_a_spot)
        if not df.empty:
            _save_cache("realtime/realtime", df)
        return df
    except Exception as e:
        logger.error(f"获取实时行情失败: {e}")
        return pd.DataFrame()


def get_market_sentiment() -> Optional[float]:
    """获取市场情绪（涨跌比），失败时返回 None"""
    try:
        df = get_realtime_quotes()
        if df.empty:
            logger.warning("[data_layer] 实时行情为空，市场情绪未知")
            return None
        up_count = (df["涨跌幅"] > 0).sum()
        down_count = (df["涨跌幅"] < 0).sum()
        total = up_count + down_count
        if total == 0:
            logger.warning("[data_layer] 涨跌计数为0，市场情绪未知")
            return None
        ratio = (up_count - down_count) / total
        return max(-1, min(1, ratio))
    except Exception as e:
        logger.warning(f"[data_layer] 市场情绪获取失败: {e}")
        return None


# ========== 资金流向 ==========

def get_stock_fund_flow(code: str) -> dict:
    """获取资金流向数据

    数据源优先级：
    1. 东方财富（优先）
    2. 腾讯成交量估算（次级）
    3. akshare（兜底）

    Returns:
        dict with keys: main_net_ratio, net_inflow_5d
    """
    # 1. 东方财富 + 腾讯估算（统一入口）
    try:
        from lib.data_fund_flow import get_fund_flow_simple
        result = get_fund_flow_simple(code)
        # 只要有有效数据就返回
        if result.get("main_net_ratio", 0) != 0 or result.get("net_inflow_5d", 0) != 0:
            return result
    except Exception as e:
        logger.debug(f"[data_layer] 资金流向获取失败 {code}: {e}")

    # 2. akshare 兜底
    try:
        market = "sh" if code.startswith(("6", "5")) else "sz"
        df = _retry_api_call(ak.stock_individual_fund_flow, stock=code, market=market)
        if not df.empty:
            latest = df.iloc[-1]
            main_net_ratio = 0.0
            for col in df.columns:
                if "主力净流入-净占比" in str(col):
                    main_net_ratio = _safe_float(latest[col]) / 100.0
                    break
            net_inflow_5d = 0.0
            recent = df.tail(5)
            for col in df.columns:
                if "主力净流入-净额" in str(col):
                    net_inflow_5d = recent[col].sum()
                    break
            return {"main_net_ratio": main_net_ratio, "net_inflow_5d": net_inflow_5d}
    except Exception as e:
        logger.debug(f"[data_layer] akshare 资金流向失败 {code}: {e}")

    return {"main_net_ratio": 0, "net_inflow_5d": 0}


def _batch_fund_flow(codes: list) -> dict:
    result = {c: {"main_net_ratio": 0, "net_inflow_5d": 0} for c in codes}
    try:
        df = _retry_api_call(ak.stock_fund_flow_individual, symbol="即时")
        if df.empty:
            return result
        code_col = None
        for col in df.columns:
            if "代码" in str(col):
                code_col = col
                break
        if code_col is None:
            return result
        net_col = None
        amount_col = None
        for col in df.columns:
            if "净额" in str(col):
                net_col = col
            if "成交额" in str(col):
                amount_col = col
        # 向量化处理，避免逐行iterrows
        df[code_col] = df[code_col].astype(str)
        if net_col and amount_col:
            df[net_col] = pd.to_numeric(df[net_col], errors="coerce").fillna(0)
            df[amount_col] = pd.to_numeric(df[amount_col], errors="coerce").fillna(1)
            # 按股票代码分组求和净额
            grouped = df.groupby(code_col, as_index=False).agg({
                net_col: "sum",
                amount_col: "sum"
            })
            # 向量化处理，避免 iterrows
            flow_map = {}
            if not grouped.empty and code_col in grouped.columns:
                codes = grouped[code_col].astype(str).tolist()
                net_vals = grouped[net_col].tolist()
                amounts = grouped[amount_col].tolist()
                for c, net_val, total_amount in zip(codes, net_vals, amounts):
                    flow_map[c] = {
                        "main_net_ratio": net_val / total_amount if total_amount > 0 else 0,
                        "net_inflow_5d": net_val,
                    }
        for code in codes:
            if code in flow_map:
                result[code] = flow_map[code]
    except Exception as e:
        logger.debug(f"批量资金流向获取失败: {e}")
    return result


def _batch_get_news_signals(codes: List[str]) -> Dict[str, dict]:
    """批量并行获取新闻/事件信号（解决 N+1 查询问题）

    Args:
        codes: 股票代码列表

    Returns:
        Dict[code -> signals dict]，每个 dict 包含 major_positive/earnings_positive/institutional_buy/no_negative
    """
    from lib.signal_fetcher import get_news_signals as _get_news_signals
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_get_news_signals, code): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception as e:
                logger.debug(f"[data_layer] 新闻信号获取失败 [{code}]: {e}")
                results[code] = {
                    "major_positive": False,
                    "earnings_positive": False,
                    "institutional_buy": False,
                    "no_negative": True,
                }
    return results


# 新闻/业绩信号已移至 lib/signal_fetcher.py
from lib.signal_fetcher import get_news_signals


# ========== 专项数据获取（期权/债券/宏观） ==========

def get_options_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取期权日线历史（通过 pytdx）"""
    try:
        from lib.data_options import get_options_daily
        return get_options_daily(code, days=days)
    except Exception as e:
        logger.debug(f"获取期权 {code} 失败: {e}")
        return pd.DataFrame()


def get_cb_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取可转债历史行情"""
    try:
        from lib.data_bond import get_cb_history
        return get_cb_history(code)
    except Exception as e:
        logger.debug(f"获取可转债 {code} 失败: {e}")
        return pd.DataFrame()


def get_repo_hist(code: str = "204001", days: int = 30) -> pd.DataFrame:
    """获取交易所回购历史"""
    try:
        from lib.data_bond import get_repo_history
        return get_repo_history(code=code, days=days)
    except Exception as e:
        logger.debug(f"获取回购 {code} 失败: {e}")
        return pd.DataFrame()


def get_macro_hist(indicator: str = "shibor", days: int = 30) -> pd.DataFrame:
    """获取宏观指标历史

    Args:
        indicator: shibor | lpr | yield | gold | oil | panel
        days: 天数
    """
    try:
        from lib.data_macro import (
            get_shibor, get_lpr, get_china_yield,
            get_gold, get_crude_oil, get_macro_panel,
        )
    except ImportError:
        return pd.DataFrame()

    try:
        if indicator == "shibor":
            return get_shibor(days=days)
        elif indicator == "lpr":
            return get_lpr(days=days)
        elif indicator == "yield":
            return get_china_yield(days=days)
        elif indicator == "gold":
            return get_gold(days=days)
        elif indicator == "oil":
            return get_crude_oil(days=days)
        elif indicator == "panel" or indicator == "all":
            return get_macro_panel(days=days)
        else:
            logger.warning(f"未知宏观指标: {indicator}")
            return pd.DataFrame()
    except Exception as e:
        logger.debug(f"获取宏观指标 {indicator} 失败: {e}")
        return pd.DataFrame()


# ========== 市场统计数据（ScJyData_zbca）==========

def get_market_stats(days: int = 30) -> pd.DataFrame:
    """获取市场统计面板（ScJyData_zbca）

    包含：期货持仓(IH5/IF6/IC7)、担保品(DBZJ)、龙虎榜(LHB)、涨跌停(ZT)等指标
    """
    from lib.data_scjy import get_market_stats as _get
    cache_key = f"scjy/market_stats/{days}"
    cached = _load_cache(cache_key, max_age_hours=24)
    if not cached.empty:
        return cached
    df = _get(days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_latest_market_stats() -> dict:
    """获取最新市场统计（dict 格式）"""
    from lib.data_scjy import get_latest_stats as _get_latest
    cache_key = "scjy/latest"
    cached = _load_cache(cache_key, max_age_hours=24)
    if not cached.empty:
        return {col: cached.iloc[0][col] for col in cached.columns}
    result = _get_latest()
    if result:
        # 缓存 dict 格式（转单行 DataFrame）
        _save_cache(cache_key, pd.DataFrame([result]))
    return result


# ========== 高频Tick数据（tdxgp）==========

def get_gp_data(code: str, market: str = "sz",
                record_types: list = None) -> pd.DataFrame:
    """获取高频Tick数据

    Args:
        code: 股票代码（6位，如 "000001"）
        market: 市场 sh/sz/bj
        record_types: 可选，只解析指定类型（如 [1, 47]）
                     1 = 年报/季报财务（0x01）
                     47 = 最近日线（0x2F）
                     3 = 日线指标A（0x03）
    """
    from lib.data_tdx_gp import get_gp_data as _get_gp
    return _get_gp(code, market, record_types=record_types)


# ========== 财务报表（cw/gpcw）==========

def get_stock_financial(code: str) -> pd.DataFrame:
    """获取股票财务数据

    数据来源：TDX 本地 gpcw*.dat 财务报表
    返回：field_index, value_main, value_aux, raw_bytes

    注意：字段含义（收入/利润/EPS等）尚未完全解码。
    具体含义参考 lib/data_tdx_fin.py 头部注释。
    """
    from lib.data_tdx_fin import GpcwReader
    cache_key = f"fin/cw/{code}"
    cached = _load_cache(cache_key, max_age_hours=168)  # 财务报表季度更新，缓存7天
    if not cached.empty:
        return cached

    reader = GpcwReader()
    df = reader.read_stock(code)
    if not df.empty:
        _save_cache(cache_key, df)
    return df

def get_financial_codes() -> list:
    """获取所有有财务数据的股票代码"""
    from lib.data_tdx_fin import GpcwReader
    reader = GpcwReader()
    return reader.read_all_codes()

def get_financial_file_info() -> dict:
    """获取当前财务数据文件信息"""
    from lib.data_tdx_fin import GpcwReader
    reader = GpcwReader()
    return reader.get_file_info()


# ========== 板块映射（infoharbor_block）==========

_block_cache = None
_stock_to_blocks_cache = None


def _load_block_mapping():
    """加载 infoharbor_block.dat 板块映射"""
    global _block_cache, _stock_to_blocks_cache
    if _block_cache is not None:
        return

    from pathlib import Path
    block_file = Path("D:/TDX/vipdoc/ref/infoharbor_block.dat")
    if not block_file.exists():
        _block_cache = {}
        _stock_to_blocks_cache = {}
        return

    blocks = {}
    stock_to_blocks = {}
    current_block = None

    with open(block_file, "r", encoding="gbk", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#"):
                parts = line.split(",")
                current_block = parts[0][1:]  # remove #
                blocks[current_block] = []
            elif current_block and line:
                for item in line.split(","):
                    item = item.strip()
                    if "#" in item:
                        code = item.split("#")[1]
                        blocks[current_block].append(code)
                        if code not in stock_to_blocks:
                            stock_to_blocks[code] = []
                        stock_to_blocks[code].append(current_block)

    _block_cache = blocks
    _stock_to_blocks_cache = stock_to_blocks


def get_stock_blocks(code: str) -> List[str]:
    """获取股票所属板块列表"""
    _load_block_mapping()
    return _stock_to_blocks_cache.get(code, [])


def get_block_stocks(block_name: str) -> List[str]:
    """获取板块内所有股票代码"""
    _load_block_mapping()
    return _block_cache.get(block_name, [])


def get_all_blocks() -> Dict[str, List[str]]:
    """获取所有板块映射"""
    _load_block_mapping()
    return _block_cache


# ========== PE/PB/市值数据（tdxstat.cfg）==========

_tdxstat_cache = None


def _load_tdxstat():
    """加载 tdxstat.cfg + tdxstat2.cfg PE/PB/市值数据"""
    global _tdxstat_cache
    if _tdxstat_cache is not None:
        return

    from pathlib import Path
    stats = {}

    # tdxstat.cfg: [0]=market [1]=code [2]=div_yield [3]=PE [9]=PE_ttm [10]=PB [11]=total_mv(万元)
    stat_file = Path("D:/TDX/vipdoc/ref/tdxstat.cfg")
    if stat_file.exists():
        with open(stat_file, "r", encoding="gbk", errors="replace") as f:
            for line in f:
                fields = line.strip().split("|")
                if len(fields) < 12:
                    continue
                code = fields[1]
                try:
                    pe = float(fields[3]) if fields[3] else None
                    pb = float(fields[10]) if fields[10] else None
                    div_yield = float(fields[2]) if fields[2] else None
                except (ValueError, IndexError):
                    pe = pb = div_yield = None
                stats[code] = {"pe": pe, "pb": pb, "div_yield": div_yield}

    # tdxstat2.cfg: [0]=market [1]=code [2]=date [3]=total_mv [5]=circ_mv (万元)
    stat2_file = Path("D:/TDX/vipdoc/ref/tdxstat2.cfg")
    if stat2_file.exists():
        with open(stat2_file, "r", encoding="gbk", errors="replace") as f:
            for line in f:
                fields = line.strip().split("|")
                if len(fields) < 6:
                    continue
                code = fields[1]
                try:
                    total_mv = float(fields[3]) if fields[3] else None
                    circ_mv = float(fields[5]) if fields[5] else None
                except (ValueError, IndexError):
                    total_mv = circ_mv = None
                if code in stats:
                    stats[code]["total_mv"] = total_mv  # 单位：亿元
                    stats[code]["circ_mv"] = circ_mv
                else:
                    stats[code] = {"total_mv": total_mv, "circ_mv": circ_mv}

    _tdxstat_cache = stats


def get_stock_valuation(code: str) -> Dict:
    """获取股票估值数据（PE/PB/市值）"""
    _load_tdxstat()
    return _tdxstat_cache.get(code, {})


# ========== 基金持仓数据（fundstk.dat）==========

_fundstk_cache = None


def _load_fundstk():
    """加载 fundstk.dat 基金持仓明细"""
    global _fundstk_cache
    if _fundstk_cache is not None:
        return

    from pathlib import Path
    stk_file = Path("D:/TDX/vipdoc/ref/fundstk.dat")
    if not stk_file.exists():
        _fundstk_cache = {}
        return

    holdings = {}  # code -> {fund_count, total_weight, total_value, date}
    with open(stk_file, "r", encoding="gbk", errors="replace") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) < 4:
                continue
            code = parts[0]
            date = parts[1]
            fund_count = 0
            total_weight = 0.0
            total_value = 0.0
            for p in parts[3:]:
                if not p.strip():
                    continue
                fields = p.split(",")
                if len(fields) >= 7:
                    fund_count += 1
                    try:
                        total_value += float(fields[4]) if fields[4] else 0
                        total_weight += float(fields[6]) if fields[6] else 0
                    except ValueError:
                        pass
            if fund_count > 0:
                holdings[code] = {
                    "fund_count": fund_count,
                    "total_weight": round(total_weight, 2),
                    "total_value": round(total_value, 2),
                    "date": date,
                }

    _fundstk_cache = holdings


def get_fund_holdings(code: str) -> Dict:
    """获取股票的基金持仓数据"""
    _load_fundstk()
    return _fundstk_cache.get(code, {})


def get_fund_holdings_all() -> Dict[str, Dict]:
    """获取所有股票的基金持仓数据"""
    _load_fundstk()
    return _fundstk_cache


# ========== 除权除息数据（gbbq，pytdx 解密）==========

_gbbq_cache = None


def _load_gbbq():
    """加载 gbbq 除权除息数据（使用 pytdx GbbqReader 解密）"""
    global _gbbq_cache
    if _gbbq_cache is not None:
        return

    from pathlib import Path
    gbbq_file = Path("D:/TDX/vipdoc/ref/gbbq")
    if not gbbq_file.exists():
        _gbbq_cache = {}
        return

    try:
        from pytdx.reader import GbbqReader
        reader = GbbqReader()
        df = reader.get_df(str(gbbq_file))
        # Group by (market, code) → sorted list of events
        _gbbq_cache = {}
        for _, row in df.iterrows():
            code = row["code"]
            if code not in _gbbq_cache:
                _gbbq_cache[code] = []
            _gbbq_cache[code].append({
                "date": int(row["datetime"]),
                "category": int(row["category"]),
                "hongli": float(row["hongli_panqianliutong"]),
                "peigujia": float(row["peigujia_qianzongguben"]),
                "songzhuangu": float(row["songgu_qianzongguben"]),
                "peigu_hou": float(row["peigu_houzongguben"]),
            })
        # Sort each stock's events by date
        for code in _gbbq_cache:
            _gbbq_cache[code].sort(key=lambda x: x["date"])
    except Exception as e:
        logging.getLogger(__name__).warning(f"gbbq 加载失败: {e}")
        _gbbq_cache = {}


def get_exright_events(code: str, start_date: int = 0) -> List[Dict]:
    """获取股票的除权除息事件列表

    Args:
        code: 股票代码（如 "600519"）
        start_date: 起始日期（如 20200101），0 表示全部

    Returns:
        list of dicts with keys: date, category, hongli, songzhuangu, etc.
    """
    _load_gbbq()
    events = _gbbq_cache.get(code, [])
    if start_date > 0:
        events = [e for e in events if e["date"] >= start_date]
    return events


def get_dividend_amount(code: str, year: int = 0) -> float:
    """获取股票的年度每股分红总额

    Args:
        code: 股票代码
        year: 年份（如 2025），0 表示最近一年

    Returns:
        每股分红总额（元）
    """
    _load_gbbq()
    events = _gbbq_cache.get(code, [])
    if year > 0:
        events = [e for e in events if e["date"] // 10000 == year and e["category"] == 1]
    else:
        events = [e for e in events if e["category"] == 1]
        if events:
            latest_year = max(e["date"] for e in events) // 10000
            events = [e for e in events if e["date"] // 10000 == latest_year]
    return sum(e["hongli"] for e in events)
