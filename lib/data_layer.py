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


def _safe_float(val, default=0.0):
    """安全提取数值"""
    if val is None:
        return default
    try:
        if pd.isna(val) or np.isnan(val):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


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

_financial_cache: Dict[str, tuple] = {}

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
    if code in _financial_cache:
        data, ts = _financial_cache[code]
        if now - ts < 300:
            return data
    try:
        info_df = _retry_api_call(ak.stock_individual_info_em, symbol=code)
        if info_df is None or info_df.empty:
            _financial_cache[code] = (None, now)
            return None
        info = {}
        for _, row in info_df.iterrows():
            info[row["item"]] = row["value"]
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
        except Exception:
            pass
        _financial_cache[code] = (data, now)
        return data
    except Exception as e:
        logger.debug(f"获取 {code} 基本面数据失败: {e}")
        _financial_cache[code] = (None, now)
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


DATA_SOURCE_PRIORITY = ["baostock", "tdx", "adata", "westock", "tushare", "akshare"]


def _fetch_from_source(code: str, days: int, source: str) -> pd.DataFrame:
    if source == "baostock":
        from lib.data_baostock import get_kline as baostock_kline
        return baostock_kline(code, days=days)
    if source == "tdx":
        from lib.data_tdx import get_kline as tdx_kline
        return tdx_kline(code, days=days)
    if source == "adata":
        from lib.data_adata import get_kline as adata_kline
        return adata_kline(code, days=days)
    if source == "westock":
        from lib.data_westock import get_kline as westock_kline
        df = westock_kline(code, period="day", limit=min(days, 2000))
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
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = get_daily(ts_code, start, end)
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "trade_date": "date", "open": "open", "close": "close",
            "high": "high", "low": "low", "vol": "volume"
        })
        return df
    if source == "akshare":
        return get_stock_hist(code, days)
    logger.warning(f"未知数据源: {source}")
    return pd.DataFrame()


def get_stock_hist_multi_source(code: str, days: int = 180,
                                source: str = "auto") -> pd.DataFrame:
    """多数据源自动切换获取个股日线

    统一返回列名: date/open/high/low/close/vol
    """
    if source != "auto":
        df = _fetch_from_source(code, days, source)
        df = _normalize_columns(df)
        if not _validate_dataframe(df):
            return pd.DataFrame()
        return df
    errors = []
    for src in DATA_SOURCE_PRIORITY:
        df = _fetch_from_source(code, days, src)
        if not df.empty:
            df = _normalize_columns(df)
            if _validate_dataframe(df):
                if src != "akshare":
                    logger.debug(f"使用数据源 [{src}] 获取 {code}")
                return df
        errors.append(f"{src}: empty/invalid")
    logger.warning(f"所有数据源均无法获取 {code}: {', '.join(errors)}")
    return pd.DataFrame()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名为 date/open/high/low/close/vol"""
    if df.empty:
        return df
    # vol -> volume 统一
    if "vol" in df.columns and "volume" not in df.columns:
        df = df.rename(columns={"vol": "volume"})
    # 确保标准列存在
    rename_map = {}
    for ch, en in [("日期", "date"), ("开盘", "open"), ("收盘", "close"),
                    ("最高", "high"), ("最低", "low"), ("成交量", "volume")]:
        if ch in df.columns and en not in df.columns:
            rename_map[ch] = en
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def _validate_dataframe(df: pd.DataFrame, min_rows: int = 10, required_cols: list = None) -> bool:
    """数据完整性校验"""
    if df.empty or len(df) < min_rows:
        return False
    required_cols = required_cols or ["date", "open", "high", "low", "close", "volume"]
    return all(c in df.columns for c in required_cols)


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
    except Exception:
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
        flow_map = {}
        for _, row in df.iterrows():
            c = str(row.get(code_col, ""))
            net_val = _safe_float(row.get(net_col, 0))
            total_amount = _safe_float(row.get(amount_col, 1))
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


# ========== 新闻/业绩信号 ==========

_earnings_positive_codes: set = set()
_major_positive_codes: set = set()
_lhb_buy_codes: set = set()
_news_cache_loaded: bool = False
_news_cache_time: float = 0.0
NEWS_CACHE_TTL: int = 3600 * 4  # 4小时 TTL


def _load_news_cache():
    global _earnings_positive_codes, _major_positive_codes, _lhb_buy_codes, _news_cache_loaded, _news_cache_time
    import time
    now = time.time()
    if _news_cache_loaded and (now - _news_cache_time) < NEWS_CACHE_TTL:
        return
    try:
        from lib.data_earnings import get_earnings_forecast, get_earnings_flash
        forecast = get_earnings_forecast()
        if not forecast.empty:
            code_col = None
            type_col = None
            for col in forecast.columns:
                if "代码" in str(col):
                    code_col = col
                if "预告类型" in str(col) or "类型" in str(col):
                    type_col = col
            if code_col and type_col:
                strong = forecast[forecast[type_col].str.contains("预增|略增|扭亏", na=False)]
                _major_positive_codes = set(strong[code_col].astype(str))
        flash = get_earnings_flash()
        if not flash.empty:
            code_col = None
            growth_col = None
            for col in flash.columns:
                if "代码" in str(col):
                    code_col = col
                if "净利" in str(col) and "增长" in str(col):
                    growth_col = col
            if code_col and growth_col:
                flash[growth_col] = pd.to_numeric(flash[growth_col], errors="coerce")
                beat = flash[flash[growth_col] > 0.30]
                _earnings_positive_codes = set(beat[code_col].astype(str))
    except Exception as e:
        logger.debug(f"加载业绩数据失败: {e}")
    try:
        df = _load_cache("lhb_codes", max_age_hours=4)
        if df.empty:
            df = _retry_api_call(ak.stock_lhb_detail_em)
            if not df.empty:
                _save_cache("lhb_codes", df)
        if not df.empty:
            code_col = None
            dir_col = None
            for col in df.columns:
                if "代码" in str(col):
                    code_col = col
                if "方向" in str(col):
                    dir_col = col
            if code_col:
                if dir_col:
                    buy = df[df[dir_col] == "买入"]
                else:
                    buy = df
                _lhb_buy_codes = set(buy[code_col].astype(str).unique())
    except Exception as e:
        logger.debug(f"加载龙虎榜数据失败: {e}")
    _news_cache_loaded = True
    _news_cache_time = now


def get_news_signals(code: str) -> dict:
    _load_news_cache()
    return {
        "major_positive": code in _major_positive_codes,
        "earnings_positive": code in _earnings_positive_codes,
        "institutional_buy": code in _lhb_buy_codes,
        "no_negative": True,
    }


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
