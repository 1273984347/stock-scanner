"""债券数据源 —— 可转债 + 回购

可转债：
  - 实时：bond_zh_hs_cov_spot()
  - 历史：bond_zh_hs_cov_daily(symbol="sh010107") —— 注意需要 sh/sz 前缀

回购：
  - 历史：通过 stock_zh_a_hist 获取（上交所 204xxx / 深交所 1318xx）
  - 注意：akshare 对回购代码可能存在网络不稳定

用法：
  from lib.data.data_bond import get_cb_history, get_cb_spot, get_repo_history
"""
import logging
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


def _normalize_cb_code(code: str) -> str:
    """标准化可转债代码格式

    bond_zh_hs_cov_daily 接受 "shxxxxxx" 格式（如 "sh110072"）
    如果传入纯数字代码，尝试加上 sh 前缀
    """
    code = code.strip()
    if code.startswith("sh") or code.startswith("sz"):
        return code
    if len(code) == 6 and code.isdigit():
        return f"sh{code}"
    return code


# ========== 可转债 ==========

def get_cb_spot() -> pd.DataFrame:
    """获取可转债实时行情（全部）"""
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        df = ak.bond_zh_hs_cov_spot()
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.debug(f"获取可转债实时行情失败: {e}")
        return pd.DataFrame()


def get_cb_history(code: str) -> pd.DataFrame:
    """获取单只可转债历史行情

    Args:
        code: 可转债代码（如 "113009" 或 "sh010107"）
              注意：需要 sh/sz 前缀格式（如 "sh010107"）
    """
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        # 标准化代码格式
        tdx_code = _normalize_cb_code(code)
        if not tdx_code:
            return pd.DataFrame()

        df = ak.bond_zh_hs_cov_daily(symbol=tdx_code)
        if df is None or df.empty:
            return pd.DataFrame()

        # 统一列名
        col_map = {}
        for ch, en in [("date", "date"), ("open", "open"), ("close", "close"),
                        ("high", "high"), ("low", "low"), ("volume", "volume")]:
            if ch in df.columns and en not in df.columns:
                col_map[ch] = en
        if col_map:
            df = df.rename(columns=col_map)

        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        return df[["date", "open", "high", "low", "close", "volume"]].copy()

    except Exception as e:
        logger.debug(f"获取可转债 {code} 历史失败: {e}")
        return pd.DataFrame()


def get_cb_basic() -> pd.DataFrame:
    """获取可转债列表（实时行情快照）"""
    return get_cb_spot()


# ========== 回购/债券 ==========

def get_repo_history(code: str = "204001", days: int = 30) -> pd.DataFrame:
    """获取交易所国债回购历史

    Args:
        code: 回购代码
              204001 = GC001（上交所1天）
              204002 = GC002（上交所2天）
              131810 = R-001（深交所1天）
        days: 天数

    Returns:
        DataFrame with columns: date, open, high, low, close, volume, amount, pct_chg
    """
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        # 标准化代码（去掉 sh/sz 前缀）
        code = code.replace("sh", "").replace("sz", "").strip()

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start,
            end_date=end,
            adjust=""
        )

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "涨跌幅": "pct_chg",
        })

        for c in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        return df[["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]].copy()

    except Exception as e:
        logger.debug(f"获取回购 {code} 历史失败: {e}")
        return pd.DataFrame()


def get_repo_rates(days: int = 30) -> pd.DataFrame:
    """获取银行间回购利率"""
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    try:
        df = ak.bond_zh_us_rate()
        if df is None or df.empty:
            return pd.DataFrame()

        if len(df) > days:
            df = df.head(days)

        return df

    except Exception as e:
        logger.debug(f"获取回购利率失败: {e}")
        return pd.DataFrame()
