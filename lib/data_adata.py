"""adata 数据源封装 —— 多数据源融合（同花顺/东方财富/新浪/腾讯/百度）

来源：1nchaos/adata (GitHub 4.5k stars)
安装：pip install adata
特性：自动多数据源切换 + 自动代理切换，高可用性
"""
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def _to_adata_code(code: str) -> str:
    """转换股票代码格式：000001 → 000001, 600519 → 600519"""
    code = code.strip().upper()
    for prefix in ["SH", "SZ", "BJ", "SH.", "SZ.", "BJ."]:
        code = code.replace(prefix, "")
    return code


def _resolve_end_date(as_of_date: Optional[str] = None):
    """回测截止日（纯函数，便于离线契约测试）。

    不传 as_of_date = 实时模式（用今天）；传了 = 回测（截止该日，修复前视偏差）。
    """
    if as_of_date is not None:
        return pd.Timestamp(as_of_date)
    return datetime.now()


def get_kline(code: str, days: int = 180, period: str = "daily", as_of_date: Optional[str] = None) -> pd.DataFrame:
    """获取 K 线数据

    §4.2 P0 修复 (2026-06-08, lib-incremental-audit §4.2):
    - 旧实现 end = datetime.now() 走 wall-clock
    - 修复: as_of_date 接受, 替换 datetime.now()

    Args:
        code: 股票代码（如 "000001"）
        days: 获取天数
        period: daily/weekly/monthly
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。
    """
    try:
        import adata.stock.market as mkt
        acode = _to_adata_code(code)
        # P0 修复 (2026-11-07)：原来**收了 as_of_date 却仍用 datetime.now()** =>
        # 回测仍取到 T 日数据 = 前视偏差。不传 = 实时模式（行为不变）。
        end = _resolve_end_date(as_of_date)
        start = end - timedelta(days=days)
        freq_map = {"daily": 1, "weekly": 2, "monthly": 3}
        df = mkt.get_market(stock_code=acode, start_date=start.strftime("%Y-%m-%d"),
                            end_date=end.strftime("%Y-%m-%d"),
                            k_type=freq_map.get(period, 1))
        if df.empty:
            return pd.DataFrame()
        # 统一列名
        col_map = {
            "tradeDate": "date", "tradeDate_stock": "date",
            "open": "open", "close": "close",
            "high": "high", "low": "low",
            "volume": "volume", "turnover": "amount",
            "changePct": "pct_chg", "changePct_stock": "pct_chg",
            "turnoverRate": "turnover", "turnoverRate_stock": "turnover",
        }
        rename = {}
        for c in df.columns:
            if c in col_map:
                rename[c] = col_map[c]
        if rename:
            df = df.rename(columns=rename)
        # 确保列存在
        for c in ["open", "close", "high", "low", "volume"]:
            if c not in df.columns and c in rename.values():
                pass
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as e:
        logger.error(f"adata 获取 {code} K线失败: {e}")
        return pd.DataFrame()


def get_realtime_quotes() -> pd.DataFrame:
    """获取实时行情

    Returns:
        DataFrame with columns: 代码, 名称, 最新价, 涨跌幅, ...
    """
    try:
        import adata.stock.market as mkt
        df = mkt.list_market_current()
        if df.empty:
            return pd.DataFrame()
        # 解析列名
        col_map = {
            "code": "代码", "shortName": "名称",
            "latestPrice": "最新价", "changePct": "涨跌幅",
            "volume": "成交量", "turnover": "成交额",
            "open": "开盘", "high": "最高", "low": "最低",
            "preClose": "昨收", "amount": "成交额",
            "pe": "市盈率", "pb": "市净率",
            "marketValue": "总市值", "circulateValue": "流通市值",
        }
        rename = {}
        for c in df.columns:
            if c in col_map:
                rename[c] = col_map[c]
        if rename:
            df = df.rename(columns=rename)
        return df
    except Exception as e:
        logger.error(f"adata 获取实时行情失败: {e}")
        return pd.DataFrame()


def get_capital_flow(code: str, days: int = 30) -> pd.DataFrame:
    """获取资金流向"""
    try:
        import adata.stock.market as mkt
        acode = _to_adata_code(code)
        df = mkt.get_capital_flow(code=acode)
        if df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.error(f"adata 获取资金流向失败 {code}: {e}")
        return pd.DataFrame()


def get_market_index() -> pd.DataFrame:
    """获取主要指数实时行情"""
    try:
        import adata.stock.market as mkt
        df = mkt.get_market_index_current()
        if df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.error(f"adata 获取指数行情失败: {e}")
        return pd.DataFrame()


def get_stock_list() -> pd.DataFrame:
    """获取股票列表"""
    try:
        import adata.stock.info as sinfo
        df = sinfo.all_code()
        if df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.error(f"adata 获取股票列表失败: {e}")
        return pd.DataFrame()


def get_concept_stocks(concept_code: str) -> pd.DataFrame:
    """获取概念板块成分股"""
    try:
        import adata.stock.info as sinfo
        df = sinfo.concept_constituent_east(concept_code=concept_code)
        return df
    except Exception as e:
        logger.error(f"adata 获取概念成分股失败: {e}")
        return pd.DataFrame()


def get_trade_calendar() -> pd.DataFrame:
    """获取交易日历"""
    try:
        import adata.stock.info as sinfo
        df = sinfo.trade_calendar()
        return df
    except Exception as e:
        logger.error(f"adata 获取交易日历失败: {e}")
        return pd.DataFrame()
