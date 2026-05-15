"""BaoStock 数据源 —— 免费无限制，绕过 EM API 阻断

安装：pip install baostock
文档：http://baostock.com/
特点：免费、无频率限制、前复权/后复权/不复权
"""
import pandas as pd
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def get_kline(code: str, days: int = 180, period: str = "daily") -> pd.DataFrame:
    """获取 K 线数据，兼容现有 data_ 系列接口

    Args:
        code: 股票代码（如 "000001"），会自动补全为 baostock 格式
        days: 获取天数
        period: daily/weekly/monthly

    Returns:
        DataFrame with columns: date, open, close, high, low, volume, amount, pct_chg
    """
    import baostock as bs

    bs_code = _to_bs_code(code)
    end = datetime.now()
    start = end - timedelta(days=days + 30)  # 多取 30 天确保覆盖

    freq_map = {"daily": "d", "weekly": "w", "monthly": "m"}
    frequency = freq_map.get(period, "d")
    adjust_map = {"1": "1", "2": "2", "3": "3"}  # 不复权/前复权/后复权
    adjust_flag = "2"  # 默认前复权

    lg = bs.login()
    if lg.error_code != "0":
        logger.error(f"BaoStock 登录失败: {lg.error_msg}")
        return pd.DataFrame()

    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            fields="date,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            frequency=frequency,
            adjustflag=adjust_flag,
        )

        if rs.error_code != "0":
            logger.error(f"BaoStock 查询失败: {rs.error_msg}")
            return pd.DataFrame()

        data = []
        while (rs.error_code == "0") and rs.next():
            row = rs.get_row_data()
            if row[10] == "1":  # tradestatus == 1 (正常交易)
                data.append(row)

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=[
            "date", "open", "high", "low", "close", "preclose",
            "volume", "amount", "adjustflag", "turnover", "tradestatus", "pct_chg"
        ])

        # 类型转换
        for col in ["open", "high", "low", "close", "preclose", "volume", "amount", "turnover", "pct_chg"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 统一列名（与现有 data_ 系列保持一致）
        df = df.rename(columns={
            "pct_chg": "pct_chg",
        })
        df = df[["date", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]]

        # 只保留指定天数
        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.error(f"BaoStock 获取 {code} 失败: {e}")
        return pd.DataFrame()
    finally:
        bs.logout()


def _to_bs_code(code: str) -> str:
    """转换为 baostock 代码格式

    BaoStock 格式: sh.600001 / sz.000001 / sz.300001 / sh.688001
    """
    code = code.strip()
    # 如果已有前缀
    if code.startswith("sh") or code.startswith("sz"):
        return f"{code[:2]}.{code[2:]}"
    # 判断市场
    if code.startswith("6") or code.startswith("9"):
        return f"sh.{code}"
    elif code.startswith("0") or code.startswith("3") or code.startswith("2"):
        return f"sz.{code}"
    elif code.startswith("4") or code.startswith("8"):
        return f"bj.{code}"
    else:
        return f"sz.{code}"  # 默认深圳
