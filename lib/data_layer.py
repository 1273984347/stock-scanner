"""数据层：多源数据获取、缓存、限流"""
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
import logging
import threading
import time

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

def _cache_path(key: str) -> Path:
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
    except Exception:
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


# ========== 基本面数据获取 ==========

_financial_cache = LRUCache(maxsize=500)  # LRU缓存，容量500


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("亿", "").replace("万", ""))
    except (ValueError, TypeError):
        return None


def _get_financial_data(code: str) -> Optional[pd.Series]:
    """获取个股基本面数据（PE/PB/市值/质押率等），5 分钟缓存"""
    now = time.time()
    cached = _financial_cache.get(code)
    if cached is not None:
        data, ts = cached
        if now - ts < 300:
            return data
    try:
        info_df = _retry_api_call(ak.stock_individual_info_em, symbol=code)
        if info_df is None or info_df.empty:
            return None
        info = {}
        if not info_df.empty and "item" in info_df.columns and "value" in info_df.columns:
            # 向量化处理，避免 iterrows
            info = dict(zip(info_df["item"].astype(str), info_df["value"]))
        data = pd.Series({
            "pe": _to_float(info.get("市盈率-动态")),
            "pb": _to_float(info.get("市净率")),
            "market_cap": _to_float(info.get("总市值")),
            "pledge_ratio": None, "net_assets": None,
            "revenue_growth": None, "prev_revenue_growth": None,
        })
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
                        data["revenue_growth"] = _to_float(rd[4]) or _to_float(rd[5])
                rs = bs.query_balance_data(bs_code, year=year, quarter=1)
                while (rs.error_code == "0") and rs.next():
                    rd = rs.get_row_data()
                    if len(rd) >= 6:
                        data["net_assets"] = _to_float(rd[5])
                bs.logout()
        except Exception as e:
            logger.warning(f"[data_layer] baostock 营收/净资产查询失败: {e}")
        _financial_cache[code] = (data, now)
        return data
    except Exception as e:
        logger.debug(f"获取 {code} 基本面数据失败: {e}")
        return None


# ========== 日线数据获取 ==========

def get_stock_hist(code: str, days: int = 180) -> pd.DataFrame:
    """用 akshare 获取个股日线（带缓存）"""
    cache_key = f"hist_{code}_{days}"
    cached = _load_cache(cache_key, max_age_hours=4)
    if not cached.empty:
        return cached
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


# ========== 实时行情 ==========

def get_realtime_quotes() -> pd.DataFrame:
    cached = _load_cache("realtime", max_age_hours=0.5)
    if not cached.empty:
        return cached
    try:
        df = _retry_api_call(ak.stock_zh_a_spot)
        if not df.empty:
            _save_cache("realtime", df)
        return df
    except Exception as e:
        logger.error(f"获取实时行情失败: {e}")
        return pd.DataFrame()


def get_market_sentiment() -> float:
    try:
        df = get_realtime_quotes()
        if df.empty:
            return 0
        up_count = (df["涨跌幅"] > 0).sum()
        down_count = (df["涨跌幅"] < 0).sum()
        total = up_count + down_count
        if total == 0:
            return 0
        ratio = (up_count - down_count) / total
        return max(-1, min(1, ratio))
    except Exception as e:
        logger.warning(f"[data_layer] 市场情绪获取失败: {e}")
        return 0


# ========== 资金流向 ==========

def get_stock_fund_flow(code: str) -> dict:
    try:
        market = "sh" if code.startswith(("6", "5")) else "sz"
        df = _retry_api_call(ak.stock_individual_fund_flow, stock=code, market=market)
        if df.empty:
            return {"main_net_ratio": 0, "net_inflow_5d": 0}
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
        logger.debug(f"获取 {code} 资金流向失败: {e}")
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
