"""BaoStock 数据源 —— 免费无限制，绕过 EM API 阻断

安装：pip install baostock
文档：http://baostock.com/
特点：免费、无频率限制、前复权/后复权/不复权
"""
import pandas as pd
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _retry_baostock(func, *args, max_retries=3, **kwargs):
    """重试 baostock 调用（指数退避）

    只重试瞬态错误（网络超时、连接断开），
    不重试永久错误（无效代码等）。
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                logger.debug(f"baostock retry {attempt+1}/{max_retries} after {wait}s: {e}")
                time.sleep(wait)
    raise last_error


def get_kline(code: str, days: int = 180, period: str = "daily", as_of_date: Optional[str] = None) -> pd.DataFrame:
    """获取 K 线数据，兼容现有 data_ 系列接口

    §4.2 P0 修复 (2026-06-08, lib-incremental-audit §4.2):
    - 旧实现 end = datetime.now() 走 wall-clock
    - backtest 走 data_baostock.get_kline 路径 = 拉 T 日数据 = 前视偏差
    - 修复: as_of_date 接受 (YYYY-MM-DD), 替换 datetime.now()
    - 不传 = 实时模式 (scanner 兼容), 传 = 回测

    Args:
        code: 股票代码（如 "000001"），会自动补全为 baostock 格式
        days: 获取天数
        period: daily/weekly/monthly
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。
    """
    import baostock as bs

    bs_code = _to_bs_code(code)
    # P0 修复: as_of_date 端点
    if as_of_date:
        end = datetime.strptime(as_of_date, "%Y-%m-%d")
    else:
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
        def _query():
            rs = bs.query_history_k_data_plus(
                bs_code,
                fields="date,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg",
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                frequency=frequency,
                adjustflag=adjust_flag,
            )

            if rs.error_code != "0":
                raise ConnectionError(f"BaoStock 查询失败: {rs.error_msg}")

            data = []
            while (rs.error_code == "0") and rs.next():
                row = rs.get_row_data()
                if row[10] == "1":  # tradestatus == 1 (正常交易)
                    data.append(row)
            return data

        data = _retry_baostock(_query)

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

    BaoStock 格式: sh.600001 / sz.000001 / sz.300001 / sh.688001 / bj.430017
    """
    code = code.strip()
    # 如果已有前缀
    if code.startswith(("sh", "sz", "bj")):
        return f"{code[:2]}.{code[2:]}"
    # 用 helper 替代手写市场判断（修复 bj 静默丢失）
    from lib.data.market_utils import detect_market
    market = detect_market(code)
    if market == "unknown":
        return f"sz.{code}"  # 默认深圳（保持向后兼容）
    return f"{market}.{code}"
