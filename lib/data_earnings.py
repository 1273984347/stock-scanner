"""财报追踪数据源 —— AKShare 财报日历/业绩预告/快报

来源：财报追踪 skill
直接调用 AKShare 函数，无需 Node.js
"""
import akshare as ak
import pandas as pd
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_earnings_calendar() -> pd.DataFrame:
    """获取财报披露时间表

    Returns:
        DataFrame: 股票代码, 名称, 预计披露日期, 实际披露日期, ...
    """
    try:
        return ak.stock_yysj_em()
    except Exception as e:
        logger.error(f"获取财报日历失败: {e}")
        return pd.DataFrame()


def get_earnings_forecast() -> pd.DataFrame:
    """获取业绩预告

    Returns:
        DataFrame: 股票代码, 名称, 预告类型, 预告净利润, 预告幅度, ...
    """
    try:
        return ak.stock_yjyg_em()
    except Exception as e:
        logger.error(f"获取业绩预告失败: {e}")
        return pd.DataFrame()


def get_earnings_flash() -> pd.DataFrame:
    """获取业绩快报

    Returns:
        DataFrame: 股票代码, 名称, 营业收入, 净利润, 每股收益, ...
    """
    try:
        return ak.stock_yjkb_em()
    except Exception as e:
        logger.error(f"获取业绩快报失败: {e}")
        return pd.DataFrame()


def get_earnings_report() -> pd.DataFrame:
    """获取完整财报数据

    Returns:
        DataFrame: 股票代码, 名称, 营业收入, 净利润, 每股收益, ROE, ...
    """
    try:
        return ak.stock_yjbb_em()
    except Exception as e:
        logger.error(f"获取财报数据失败: {e}")
        return pd.DataFrame()


def get_upcoming_earnings(days: int = 7) -> pd.DataFrame:
    """获取即将披露财报的股票

    Args:
        days: 未来天数

    Returns:
        过滤后的 DataFrame
    """
    df = get_earnings_calendar()
    if df.empty:
        return df

    # 尝试过滤日期
    try:
        if "预计披露日期" in df.columns:
            df["预计披露日期"] = pd.to_datetime(df["预计披露日期"], errors="coerce")
            today = pd.Timestamp.now()
            future = today + pd.Timedelta(days=days)
            mask = (df["预计披露日期"] >= today) & (df["预计披露日期"] <= future)
            return df[mask].sort_values("预计披露日期")
    except Exception as e:
        logger.warning(f"日期过滤失败: {e}")

    return df


def get_earnings_surprise() -> pd.DataFrame:
    """获取业绩超预期/不及预期的股票

    结合业绩预告和快报数据
    """
    forecast = get_earnings_forecast()
    flash = get_earnings_flash()

    if forecast.empty and flash.empty:
        return pd.DataFrame()

    # 从预告中筛选大幅预增的
    surprises = []
    if not forecast.empty and "预告类型" in forecast.columns:
        strong_up = forecast[forecast["预告类型"].str.contains("预增|略增|扭亏", na=False)]
        if not strong_up.empty:
            surprises.append(strong_up)

    if not flash.empty and "净利润同比增长" in flash.columns:
        flash["净利润同比增长"] = pd.to_numeric(flash["净利润同比增长"], errors="coerce")
        beat = flash[flash["净利润同比增长"] > 0.30]
        if not beat.empty:
            surprises.append(beat)

    if surprises:
        return pd.concat(surprises, ignore_index=True).drop_duplicates()
    return pd.DataFrame()
