"""新闻/业绩信号模块：业绩预告、龙虎榜买入信号

依赖：lib/cache.py, lib/data_earnings.py
"""
import akshare as ak
import pandas as pd
from typing import Dict, Optional
import logging
import threading
import time

from lib.cache import retry_api_call, load_cache, save_cache

logger = logging.getLogger(__name__)

# ========== 新闻/业绩信号 ==========

_earnings_positive_codes: set = set()
_major_positive_codes: set = set()
_lhb_buy_codes: set = set()
_news_cache_loaded: bool = False
_news_cache_time: float = 0.0
_news_cache_lock = threading.Lock()
NEWS_CACHE_TTL: int = 3600 * 4  # 4小时 TTL


def _load_news_cache():
    global _earnings_positive_codes, _major_positive_codes, _lhb_buy_codes, _news_cache_loaded, _news_cache_time
    now = time.time()
    with _news_cache_lock:
        if _news_cache_loaded and (now - _news_cache_time) < NEWS_CACHE_TTL:
            return
    try:
        from lib.data_earnings import get_earnings_forecast, get_earnings_flash
        forecast = get_earnings_forecast()
        major_codes = set()
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
                major_codes = set(strong[code_col].astype(str))
        flash = get_earnings_flash()
        earnings_codes = set()
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
                earnings_codes = set(beat[code_col].astype(str))
        with _news_cache_lock:
            _major_positive_codes = major_codes
            _earnings_positive_codes = earnings_codes
    except Exception as e:
        logger.debug(f"加载业绩数据失败: {e}")
    try:
        df = load_cache("lhb_codes", max_age_hours=4)
        if df.empty:
            df = retry_api_call(ak.stock_lhb_detail_em)
            if not df.empty:
                save_cache("lhb_codes", df)
        lhb_codes = set()
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
                lhb_codes = set(buy[code_col].astype(str).unique())
        with _news_cache_lock:
            _lhb_buy_codes = lhb_codes
    except Exception as e:
        logger.debug(f"加载龙虎榜数据失败: {e}")
    with _news_cache_lock:
        _news_cache_loaded = True
        _news_cache_time = now


def get_news_signals(code: str) -> dict:
    """获取个股的新闻/事件信号

    Returns:
        dict: {
            "major_positive": bool,  # 业绩预告预增/略增/扭亏
            "earnings_positive": bool,  # 业绩超预期（净利润增长>30%）
            "institutional_buy": bool,  # 近期有机构龙虎榜买入
            "no_negative": bool,  # 无负面消息（默认True，需扩展）
        }
    """
    _load_news_cache()
    with _news_cache_lock:
        return {
            "major_positive": code in _major_positive_codes,
            "earnings_positive": code in _earnings_positive_codes,
            "institutional_buy": code in _lhb_buy_codes,
            "no_negative": True,
        }
