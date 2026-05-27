"""新闻/业绩/龙虎榜信号获取模块

从 data_layer.py 提取的业务逻辑，负责：
- 业绩预告/快报信号
- 龙虎榜买入席位信号
- 新闻信号缓存管理
"""
import pandas as pd
import time
import logging
from typing import Dict

logger = logging.getLogger(__name__)

# ========== 信号缓存 ==========

_earnings_positive_codes: set = set()
_major_positive_codes: set = set()
_lhb_buy_codes: set = set()
_news_cache_loaded: bool = False
_news_cache_time: float = 0.0
NEWS_CACHE_TTL: int = 3600 * 4  # 4小时 TTL


def _load_earnings_cache():
    """加载业绩预告和业绩快报数据"""
    global _major_positive_codes, _earnings_positive_codes
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


def _load_lhb_cache():
    """加载龙虎榜买入席位数据"""
    global _lhb_buy_codes
    try:
        from lib.data_layer import _load_cache, _retry_api_call, _save_cache
        import akshare as ak

        df = _load_cache("lhb/codes", max_age_hours=4)
        if df.empty:
            df = _retry_api_call(ak.stock_lhb_detail_em)
            if not df.empty:
                _save_cache("lhb/codes", df)
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


def _load_news_cache():
    """加载新闻信号缓存（主函数）"""
    global _news_cache_loaded, _news_cache_time
    now = time.time()
    if _news_cache_loaded and (now - _news_cache_time) < NEWS_CACHE_TTL:
        return
    _load_earnings_cache()
    _load_lhb_cache()
    _news_cache_loaded = True
    _news_cache_time = now


def get_news_signals(code: str) -> dict:
    """获取个股的新闻/业绩/龙虎榜信号

    Args:
        code: 股票代码

    Returns:
        包含以下键的字典:
        - major_positive: 业绩预告类型为预增/略增/扭亏
        - earnings_positive: 业绩快报净利润增长 > 30%
        - institutional_buy: 龙虎榜有买入席位
        - no_negative: 无负面消息（目前始终为 True）
    """
    _load_news_cache()
    return {
        "major_positive": code in _major_positive_codes,
        "earnings_positive": code in _earnings_positive_codes,
        "institutional_buy": code in _lhb_buy_codes,
        "no_negative": True,
    }


def preload_news_signals():
    """预加载新闻信号缓存（后台调用）"""
    _load_news_cache()
