"""期权数据源 —— 通过 pytdx hq API 获取

支持：上交所/深交所 ETF 期权（50ETF期权、300ETF期权等）

配置：
  TDX_HOST = 180.153.39.210（默认）
  TDX_PORT = 7709（默认）
"""
import os
import logging
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

TDX_HOST = os.environ.get("TDX_HOST", "180.153.39.210")
TDX_PORT = int(os.environ.get("TDX_PORT", "7709"))


def _to_option_market(code: str) -> int:
    """期权市场代码
    1000开头 → 上海（1）
    9000开头 → 深圳（0）
    """
    code = code.strip().zfill(11)
    if code.startswith("1000"):
        return 1
    return 0


def get_options_list() -> pd.DataFrame:
    """获取期权列表（上交所/深交所）"""
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return pd.DataFrame()

    api = TdxHq_API(heartbeat=True, auto_retry=True)
    try:
        api.connect(TDX_HOST, TDX_PORT, timeouts=5)
        result = []

        # 上交所期权（1000开头）
        for i in range(20):
            data = api.get_security_list(1, i * 500)
            if data:
                for item in data:
                    code = str(item.get("code", ""))
                    if code.startswith("1000"):
                        result.append({
                            "code": code,
                            "name": item.get("name", ""),
                            "market": "SH",
                        })

        # 深交所期权（9000开头）
        for i in range(20):
            data = api.get_security_list(0, i * 500)
            if data:
                for item in data:
                    code = str(item.get("code", ""))
                    if code.startswith("9000"):
                        result.append({
                            "code": code,
                            "name": item.get("name", ""),
                            "market": "SZ",
                        })

        return pd.DataFrame(result)
    except Exception as e:
        logger.debug(f"获取期权列表失败: {e}")
        return pd.DataFrame()
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


def get_options_daily(code: str, start_date: str = None, end_date: str = None,
                      days: int = 180) -> pd.DataFrame:
    """获取期权日线历史

    Args:
        code: 期权代码（如 "10004320" 代表 50ETF沽3月2850）
        start_date: 开始日期 "YYYYMMDD"（可选）
        end_date: 结束日期 "YYYYMMDD"（可选）
        days: 获取天数（默认180天）

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return pd.DataFrame()

    api = TdxHq_API(heartbeat=True, auto_retry=True)
    try:
        api.connect(TDX_HOST, TDX_PORT, timeouts=5)
        market = _to_option_market(code)
        code = code.strip().zfill(11)

        count = min(days + 50, 800)
        data = api.get_security_bars(4, market, code, 0, count)

        if data is None or len(data) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        if "date" in df.columns:
            df = df.rename(columns={"date": "date"})
            df["date"] = pd.to_datetime(df["date"], errors="coerce")

        col_map = {
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume", "amount": "amount",
        }
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # 按日期过滤
        if start_date:
            start_dt = pd.to_datetime(start_date, format="%Y%m%d", errors="coerce")
            df = df[df["date"] >= start_dt]
        if end_date:
            end_dt = pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")
            df = df[df["date"] <= end_dt]

        return df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()

    except Exception as e:
        logger.debug(f"获取期权日线 {code} 失败: {e}")
        return pd.DataFrame()
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


# ========== 常用期权快捷函数 ==========

def get_50etf_options_daily(expiry_month: str = None, option_type: str = "call",
                             strike: int = None, days: int = 180) -> pd.DataFrame:
    """获取 50ETF 期权

    Args:
        expiry_month: 到期月（如 "202506" 代表 2025年6月）
        option_type: "call" 或 "put"
        strike: 行权价（可选）
        days: 天数
    """
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return pd.DataFrame()

    api = TdxHq_API(heartbeat=True, auto_retry=True)
    try:
        api.connect(TDX_HOST, TDX_PORT, timeouts=5)

        # 获取所有上交所期权列表
        all_options = []
        for i in range(20):
            data = api.get_security_list(1, i * 500)
            if data:
                all_options.extend(data)

        # 过滤 50ETF 期权
        targets = []
        for item in all_options:
            code = str(item.get("code", ""))
            name = str(item.get("name", ""))
            if code.startswith("1000") and "50ETF" in name:
                if expiry_month and expiry_month not in name:
                    continue
                if option_type == "call" and "购" not in name and "认购" not in name:
                    continue
                if option_type == "put" and "沽" not in name and "认沽" not in name:
                    continue
                if strike and str(strike) not in name:
                    continue
                targets.append({"code": code, "name": name})

        if not targets:
            return pd.DataFrame()

        # 取最近一支
        target = targets[0]
        return get_options_daily(target["code"], days=days)

    except Exception as e:
        logger.debug(f"获取50ETF期权失败: {e}")
        return pd.DataFrame()
    finally:
        try:
            api.disconnect()
        except Exception:
            pass
