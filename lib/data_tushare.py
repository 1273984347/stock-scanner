"""Tushare Pro 数据源封装

来源：cn-finance-data skill（220+ 中国金融数据 API）
需要：pip install tushare，设置 TUSHARE_TOKEN 环境变量
"""
import os
import pandas as pd
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

_pro = None


def _get_pro():
    """延迟初始化 Tushare Pro API"""
    global _pro
    if _pro is None:
        try:
            import tushare as ts
            token = os.environ.get("TUSHARE_TOKEN", "")
            if not token:
                logger.error("TUSHARE_TOKEN 环境变量未设置")
                return None
            ts.set_token(token)
            _pro = ts.pro_api()
        except ImportError:
            logger.error("tushare 未安装，请运行: pip install tushare")
            return None
    return _pro


def _to_ts_code(code: str) -> str:
    """转换股票代码格式：000001 → 000001.SZ, 600519 → 600519.SH"""
    code = code.strip()
    if "." in code:
        return code
    if code.startswith(("6", "5")):
        return f"{code}.SH"
    if code.startswith(("0", "3", "2")):
        return f"{code}.SZ"
    if code.startswith(("8", "4")):
        return f"{code}.BJ"
    return code


def get_daily(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取日线行情

    Args:
        ts_code: 股票代码（如 "000001.SZ"）
        start_date: 开始日期 "YYYYMMDD"
        end_date: 结束日期 "YYYYMMDD"
    """
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.error(f"获取日线失败 {ts_code}: {e}")
        return pd.DataFrame()


def get_daily_basic(ts_code: str, trade_date: str) -> pd.DataFrame:
    """获取每日指标（PE/PB/换手率等）"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.daily_basic(ts_code=ts_code, trade_date=trade_date)
    except Exception as e:
        logger.error(f"获取每日指标失败 {ts_code}: {e}")
        return pd.DataFrame()


def get_financials(ts_code: str, period: str) -> Dict:
    """获取财务数据（利润表+资产负债表+现金流量表）

    Args:
        ts_code: 股票代码
        period: 报告期 "YYYYMMDD"（如 "20241231"）

    Returns:
        {"income": DataFrame, "balance": DataFrame, "cashflow": DataFrame}
    """
    pro = _get_pro()
    if pro is None:
        return {}
    try:
        income = pro.income(ts_code=ts_code, period=period)
        balance = pro.balancesheet(ts_code=ts_code, period=period)
        cashflow = pro.cashflow(ts_code=ts_code, period=period)
        return {"income": income, "balance": balance, "cashflow": cashflow}
    except Exception as e:
        logger.error(f"获取财务数据失败 {ts_code}: {e}")
        return {}


def get_fina_indicator(ts_code: str, period: str = None) -> pd.DataFrame:
    """获取财务指标（ROE/ROA/毛利率等）"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.fina_indicator(ts_code=ts_code, period=period)
    except Exception as e:
        logger.error(f"获取财务指标失败 {ts_code}: {e}")
        return pd.DataFrame()


def get_moneyflow(ts_code: str, trade_date: str = None) -> pd.DataFrame:
    """获取资金流向"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.moneyflow(ts_code=ts_code, trade_date=trade_date)
    except Exception as e:
        logger.error(f"获取资金流向失败 {ts_code}: {e}")
        return pd.DataFrame()


def get_index_daily(index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取指数日线"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.index_daily(ts_code=index_code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.error(f"获取指数日线失败 {index_code}: {e}")
        return pd.DataFrame()


def get_stock_basic() -> pd.DataFrame:
    """获取股票列表"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.stock_basic(exchange="", list_status="L",
                               fields="ts_code,symbol,name,area,industry,market,list_date")
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        return pd.DataFrame()


def get_top_list(trade_date: str) -> pd.DataFrame:
    """获取龙虎榜"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.top_list(trade_date=trade_date)
    except Exception as e:
        logger.error(f"获取龙虎榜失败: {e}")
        return pd.DataFrame()


def get_margin_detail(ts_code: str, trade_date: str = None) -> pd.DataFrame:
    """获取融资融券明细"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.margin_detail(ts_code=ts_code, trade_date=trade_date)
    except Exception as e:
        logger.error(f"获取融资融券失败 {ts_code}: {e}")
        return pd.DataFrame()


def get_macro_cpi() -> pd.DataFrame:
    """获取 CPI 数据"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.cn_cpi()
    except Exception as e:
        logger.error(f"获取 CPI 失败: {e}")
        return pd.DataFrame()


def get_macro_pmi() -> pd.DataFrame:
    """获取 PMI 数据"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.cn_pmi()
    except Exception as e:
        logger.error(f"获取 PMI 失败: {e}")
        return pd.DataFrame()


def get_balancesheet(ts_code: str, start_date: str = "20150101",
                     end_date: str = None) -> pd.DataFrame:
    """获取资产负债表（Tushare pro.balancesheet）

    Returns:
        pd.DataFrame: 列含 ts_code/end_date/inventory/st_borr/lt_borr/bond_payable；
        失败或 pro 不可用返回空 DataFrame
    """
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.balancesheet(
            ts_code=ts_code, start_date=start_date, end_date=end_date,
            fields="ts_code,end_date,inventory,st_borr,lt_borr,bond_payable",
        )
    except Exception as e:
        logger.error(f"获取资产负债表失败 {ts_code}: {e}")
        return pd.DataFrame()


def get_income(ts_code: str, start_date: str = "20150101",
               end_date: str = None) -> pd.DataFrame:
    """获取利润表（Tushare pro.income），列含 ts_code/end_date/revenue"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.income(ts_code=ts_code, start_date=start_date, end_date=end_date,
                          fields="ts_code,end_date,revenue")
    except Exception as e:
        logger.error(f"获取利润表失败 {ts_code}: {e}")
        return pd.DataFrame()


def get_cashflow(ts_code: str, start_date: str = "20150101",
                 end_date: str = None) -> pd.DataFrame:
    """获取现金流量表（Tushare pro.cashflow），列含 ts_code/end_date/c_pay_acq_const_fiolta"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.cashflow(ts_code=ts_code, start_date=start_date, end_date=end_date,
                            fields="ts_code,end_date,c_pay_acq_const_fiolta")
    except Exception as e:
        logger.error(f"获取现金流量表失败 {ts_code}: {e}")
        return pd.DataFrame()
