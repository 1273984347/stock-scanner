"""融资融券数据 —— 杠杆资金动向

数据源: akshare 融资融券接口
- 融资余额变化：市场整体 + 个股
- 融券余额变化：做空力量
- 融资净买入：多头情绪指标

使用方式：
    from lib.data_margin import get_margin_data, get_margin_indicator
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging
import time

logger = logging.getLogger(__name__)

# 缓存
_MARGIN_CACHE: Dict[str, pd.DataFrame] = {}
_CACHE_TTL = 3600  # 1 小时


def _safe_call(func, *args, max_retries=3, **kwargs):
    """带重试的安全 API 调用"""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.warning(f"融资融券 API 调用失败: {e}")
                return None


def get_margin_market_daily(start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """获取市场融资融券汇总数据（日频）

    Returns:
        DataFrame with columns: date, rzye(融资余额), rzmre(融资买入额),
        rzche(融资偿还额), rqye(融券余额), rqmcl(融券卖出量),
        rqchl(融券偿还量), rzrqye(融资融券余额)
    """
    cache_key = f"market_{start_date}_{end_date}"
    if cache_key in _MARGIN_CACHE:
        return _MARGIN_CACHE[cache_key]

    try:
        import akshare as ak
        df = _safe_call(ak.stock_margin_sse, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            # 备用接口
            df = _safe_call(ak.stock_margin_detail_sse, date=end_date or "")

        if df is not None and not df.empty:
            _MARGIN_CACHE[cache_key] = df
            return df
    except Exception as e:
        logger.warning(f"获取市场融资融券数据失败: {e}")

    return pd.DataFrame()


def get_margin_stock(code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """获取个股融资融券数据

    Args:
        code: 股票代码，如 "600519"
        start_date: 开始日期，格式 "20260101"
        end_date: 结束日期

    Returns:
        DataFrame with columns: date, rzye, rzmre, rzche, rqye, rqmcl, rqchl
    """
    cache_key = f"stock_{code}_{start_date}_{end_date}"
    if cache_key in _MARGIN_CACHE:
        return _MARGIN_CACHE[cache_key]

    try:
        import akshare as ak
        # 个股融资融券历史数据（SSE 或 SZSE）
        # 列名可能因 akshare 版本不同而变化，按位置索引取
        # 非交易日 API 会返回空数据，需要回退到上一个交易日
        for days_back in range(5):
            check_date = datetime.strptime(end_date, "%Y%m%d")
            check_date = check_date - timedelta(days=days_back)
            check_str = check_date.strftime("%Y%m%d")

            try:
                if code.startswith("6"):
                    df = ak.stock_margin_detail_sse(date=check_str)
                else:
                    df = ak.stock_margin_detail_szse(date=check_str)

                if df is not None and not df.empty and len(df.columns) >= 6:
                    # 按位置过滤：第2列是证券代码
                    code_col = df.columns[1]
                    df[code_col] = df[code_col].astype(str).str.zfill(6)
                    stock_df = df[df[code_col] == code]
                    if not stock_df.empty:
                        _MARGIN_CACHE[cache_key] = stock_df
                        return stock_df
                    # 有数据但没找到该股票，不再重试
                    break
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"获取 {code} 融资融券数据失败: {e}")

    return pd.DataFrame()


def get_margin_indicator(code: str, lookback_days: int = 20, as_of_date: Optional[str] = None) -> Dict:
    """计算个股融资融券指标

    P0 #4 修复 (2026-06-08, lib-incremental-audit §2.1):
    - 旧实现 end_date = datetime.now().strftime("%Y%m%d") 静默用 wall-clock
    - 旧实现 start_date = (datetime.now() - timedelta(days=lookback_days * 2))
    - backtest 走 data_layer.get_margin_indicator 路径 → 拉 T 日数据 = 前视偏差
    - 修复: as_of_date 接受 (YYYY-MM-DD), 替换 datetime.now() 端点
    - 不传 = 实时模式 (scanner 兼容), 传 = 回测

    Args:
        code: 股票代码
        lookback_days: 回溯天数
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。

    Returns:
        dict with keys:
        - margin_balance: 当前融资余额（亿元）
        - margin_balance_chg: 融资余额变化（5日/10日/20日）
        - margin_buy_ratio: 融资买入/偿还比（>1 表示净买入）
        - margin_sentiment: 杠杆情绪分（0-1，基于融资余额趋势）
        - short_balance: 融券余额（做空力量）
        - short_ratio: 融券/融资比（做空比例）
    """
    # P0 #4 修复: as_of_date 端点
    end_date = (as_of_date.replace("-", "") if as_of_date else datetime.now().strftime("%Y%m%d"))
    end_dt = datetime.strptime(end_date, "%Y%m%d")
    start_date = (end_dt - timedelta(days=lookback_days * 2)).strftime("%Y%m%d")

    df = get_margin_stock(code, start_date, end_date)
    if df.empty:
        return {
            "margin_balance": 0,
            "margin_balance_chg": {},
            "margin_buy_ratio": 1.0,
            "margin_sentiment": 0.5,
            "short_balance": 0,
            "short_ratio": 0,
        }

    # 按位置取列（akshare 列名可能因版本/编码不同而变化）
    # SSE 格式: 日期|证券代码|简称|融资买入额|融资偿还额|融资余额|融券卖出量|融券偿还量|融券余额
    # SZSE 格式类似但列数可能不同
    ncols = len(df.columns)
    if ncols < 6:
        return {
            "margin_balance": 0,
            "margin_balance_chg": {},
            "margin_buy_ratio": 1.0,
            "margin_sentiment": 0.5,
            "short_balance": 0,
            "short_ratio": 0,
        }

    # 按位置排序（第0列=日期）
    recent = df.sort_values(df.columns[0]).tail(lookback_days)
    if len(recent) < 1:
        return {
            "margin_balance": 0,
            "margin_balance_chg": {},
            "margin_buy_ratio": 1.0,
            "margin_sentiment": 0.5,
            "short_balance": 0,
            "short_ratio": 0,
        }

    # 按位置取列值（SSE: 日期|代码|简称|融资买入|融资偿还|融资余额|融券卖出|融券偿还|融券余额）
    rzye = pd.to_numeric(recent.iloc[:, 4], errors="coerce").fillna(0)  # 融资余额
    current_balance = rzye.iloc[-1] / 1e8  # 转为亿元

    # 余额变化
    balance_chg = {}
    for period in [5, 10, 20]:
        if len(rzye) >= period:
            base = rzye.iloc[-period]
            if base > 0:
                chg = (rzye.iloc[-1] - base) / base * 100
                balance_chg[f"{period}d"] = round(chg, 2)

    # 融资买入/偿还比
    buy_ratio = 1.0
    if ncols >= 4:
        buy = pd.to_numeric(recent.iloc[:, 2], errors="coerce").fillna(0).sum()   # 融资买入额
        repay = pd.to_numeric(recent.iloc[:, 3], errors="coerce").fillna(0).sum()  # 融资偿还额
        if repay > 0:
            buy_ratio = round(buy / repay, 3)

    # 融券余额
    short_balance = 0
    short_ratio = 0
    if ncols >= 8:
        rqye = pd.to_numeric(recent.iloc[:, 7], errors="coerce").fillna(0)  # 融券余额
        short_balance = rqye.iloc[-1] / 1e8
        if current_balance > 0:
            short_ratio = round(short_balance / current_balance, 4)

    # 杠杆情绪分（基于融资余额趋势）
    if len(rzye) >= 10:
        # 用线性回归斜率衡量趋势
        x = np.arange(len(rzye))
        slope = np.polyfit(x, rzye.values, 1)[0]
        # 归一化到 [0, 1]
        sentiment = min(1.0, max(0.0, 0.5 + slope / (rzye.mean() * 0.01)))
    else:
        sentiment = 0.5

    return {
        "margin_balance": round(current_balance, 2),
        "margin_balance_chg": balance_chg,
        "margin_buy_ratio": buy_ratio,
        "margin_sentiment": round(sentiment, 3),
        "short_balance": round(short_balance, 2),
        "short_ratio": short_ratio,
    }


def get_margin_top_stocks(top_n: int = 20) -> pd.DataFrame:
    """获取融资净买入排名前 N 的股票

    Returns:
        DataFrame with columns: code, name, margin_buy(融资买入), margin_repay(融资偿还),
        net_buy(净买入), balance(融资余额)
    """
    try:
        import akshare as ak
        # 获取当日融资融券明细
        df = _safe_call(ak.stock_margin_detail_sse, date=datetime.now().strftime("%Y%m%d"))
        if df is None or df.empty:
            return pd.DataFrame()

        # 计算净买入
        buy_col = next((c for c in ["融资买入额", "rzmre"] if c in df.columns), None)
        repay_col = next((c for c in ["融资偿还额", "rzche"] if c in df.columns), None)

        if buy_col and repay_col:
            df["net_buy"] = pd.to_numeric(df[buy_col], errors="coerce") - pd.to_numeric(df[repay_col], errors="coerce")
            df = df.sort_values("net_buy", ascending=False).head(top_n)

        return df
    except Exception as e:
        logger.warning(f"获取融资排名失败: {e}")
        return pd.DataFrame()


def clear_cache():
    """清除缓存"""
    _MARGIN_CACHE.clear()
