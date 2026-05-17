"""宏观数据源 —— 利率/外汇/商品

来源：akshare

支持：
  - SHIBOR/LPR 利率
  - 美元/人民币汇率
  - 黄金/原油大宗商品
  - 国债收益率

用法：
  from lib.data_macro import get_shibor, get_lpr, get_gold, get_crude_oil, get_usd_cny
"""
import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _rename_date_col(df: pd.DataFrame) -> pd.DataFrame:
    """通用日期列重命名——通过位置判断第一列为日期"""
    if df is None or df.empty:
        return df
    # 第一列通常是日期列
    first_col = df.columns[0]
    if first_col != "date":
        df = df.rename(columns={first_col: "date"})
    return df


def _numeric_cols(df: pd.DataFrame) -> pd.DataFrame:
    """将所有非日期列转为数值"""
    if df is None or df.empty:
        return df
    for c in df.columns:
        if c != "date":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ========== 利率 ==========

def get_shibor(days: int = 30) -> pd.DataFrame:
    """获取 SHIBOR（上海银行间同业拆借利率）

    列：date, O/N-银..., O/N-涨跌, 1W-银..., 1W-涨跌, ...
    """
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        df = ak.macro_china_shibor_all()
        if df is None or df.empty:
            return pd.DataFrame()

        df = _rename_date_col(df)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = _numeric_cols(df)
        df = df.sort_values("date")

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.debug(f"获取SHIBOR失败: {e}")
        return pd.DataFrame()


def get_lpr(days: int = 30) -> pd.DataFrame:
    """获取 LPR（贷款市场报价利率）

    列：date, LPR1Y, LPR5Y, RATE_1, RATE_2
    """
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        df = ak.macro_china_lpr()
        if df is None or df.empty:
            return pd.DataFrame()

        df = _rename_date_col(df)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = _numeric_cols(df)
        df = df.sort_values("date")

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.debug(f"获取LPR失败: {e}")
        return pd.DataFrame()


def get_china_yield(days: int = 30) -> pd.DataFrame:
    """获取中国国债收益率曲线"""
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        df = ak.bond_china_yield()
        if df is None or df.empty:
            return pd.DataFrame()

        df = _rename_date_col(df)
        df = _numeric_cols(df)
        df = df.sort_values("date")

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.debug(f"获取国债收益率失败: {e}")
        return pd.DataFrame()


# ========== 外汇 ==========

def get_usd_cny(days: int = 30) -> pd.DataFrame:
    """获取美元/人民币（USD/CNY）汇率"""
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        df = ak.currency_boc_sina()
        if df is None or df.empty:
            return pd.DataFrame()

        df = _rename_date_col(df)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = _numeric_cols(df)
        df = df.sort_values("date")

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.debug(f"获取USD/CNY汇率失败: {e}")
        return pd.DataFrame()


# ========== 大宗商品 ==========

def get_gold(days: int = 30) -> pd.DataFrame:
    """获取黄金价格（SGE AU9999）"""
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        df = ak.spot_golden_benchmark_sge()
        if df is None or df.empty:
            return pd.DataFrame()

        df = _rename_date_col(df)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date")

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.debug(f"获取黄金价格失败: {e}")
        return pd.DataFrame()


def get_crude_oil(days: int = 30) -> pd.DataFrame:
    """获取原油价格（布伦特/WTI）"""
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        df = ak.energy_oil_hist()
        if df is None or df.empty:
            return pd.DataFrame()

        df = _rename_date_col(df)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = _numeric_cols(df)
        df = df.sort_values("date")

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.debug(f"获取原油价格失败: {e}")
        return pd.DataFrame()


# ========== 综合宏观面板 ==========

def get_macro_panel(days: int = 30) -> pd.DataFrame:
    """获取综合宏观指标面板（利率+汇率+商品）"""
    results = {}

    shibor = get_shibor(days=days)
    if not shibor.empty:
        results["shibor"] = shibor.copy()

    lpr = get_lpr(days=days)
    if not lpr.empty:
        results["lpr"] = lpr.copy()

    gold = get_gold(days=days)
    if not gold.empty:
        results["gold"] = gold.copy()

    oil = get_crude_oil(days=days)
    if not oil.empty:
        results["oil"] = oil.copy()

    if not results:
        return pd.DataFrame()

    # 以 shibor 为基础，依次 left join 其他指标
    df = results.get("shibor")
    for name in ["lpr", "gold", "oil"]:
        if name not in results or results[name].empty:
            continue
        other = results[name].copy()
        # 重命名非日期列加前缀
        rename = {c: f"{name}_{c}" for c in other.columns if c != "date"}
        other = other.rename(columns=rename)
        df = df.merge(other, on="date", how="left")

    if df is not None and not df.empty:
        df = df.sort_values("date")

    return df if df is not None else pd.DataFrame()
