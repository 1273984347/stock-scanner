"""Ashare 极简数据源 —— 新浪+腾讯双源自动切换回退层

来源：mpquant/Ashare (GitHub 3.5k stars)
安装：下载 Ashare.py 到项目目录，零依赖
定位：当 akshare/adata 都失败时的兜底数据源
"""
import pandas as pd
import requests
import re
import logging
from datetime import datetime, timedelta
from typing import Optional, List

logger = logging.getLogger(__name__)

# ========== 腾讯接口 ==========

TENCENT_URL = "http://qt.gtimg.cn/q="

def _get_tencent_quotes(codes: List[str]) -> pd.DataFrame:
    """从腾讯接口获取实时行情"""
    if not codes:
        return pd.DataFrame()
    try:
        # 腾讯代码格式：sh600519, sz000001
        tencent_codes = []
        for c in codes:
            c = c.strip()
            if c.startswith(("sh", "sz", "bj")):
                tencent_codes.append(c)
            elif c.startswith(("6", "5")):
                tencent_codes.append(f"sh{c}")
            elif c.startswith(("0", "3", "2")):
                tencent_codes.append(f"sz{c}")
            else:
                tencent_codes.append(c)

        url = TENCENT_URL + ",".join(tencent_codes)
        resp = requests.get(url, timeout=5)
        resp.encoding = "gbk"
        text = resp.text

        rows = []
        for line in text.strip().split("\n"):
            # 解析腾讯返回格式: v_sh600519="1~贵州茅台~..."
            match = re.search(r'"(.*)"', line)
            if not match:
                continue
            parts = match.group(1).split("~")
            if len(parts) < 40:
                continue
            try:
                row = {
                    "代码": parts[2],
                    "名称": parts[1],
                    "最新价": float(parts[3]) if parts[3] else 0,
                    "涨跌幅": float(parts[32]) if parts[32] else 0,
                    "昨收": float(parts[4]) if parts[4] else 0,
                    "开盘": float(parts[5]) if parts[5] else 0,
                    "最高": float(parts[33]) if parts[33] else 0,
                    "最低": float(parts[34]) if parts[34] else 0,
                    "成交量": float(parts[6]) if parts[6] else 0,
                    "成交额": float(parts[37]) if parts[37] else 0,
                    "换手率": float(parts[38]) if parts[38] else 0,
                    "市盈率": float(parts[39]) if parts[39] else 0,
                    "市净率": float(parts[46]) if len(parts) > 46 and parts[46] not in ("", "-", "0") else None,
                    "总市值": float(parts[44]) if len(parts) > 44 and parts[44] not in ("", "-", "0") else None,
                }
                rows.append(row)
            except (ValueError, IndexError):
                continue

        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        logger.warning(f"腾讯行情接口失败: {e}")
        return pd.DataFrame()


# ========== 新浪接口 ==========

SINA_URL = "http://hq.sinajs.cn/list="

def _get_sina_quotes(codes: List[str]) -> pd.DataFrame:
    """从新浪接口获取实时行情"""
    if not codes:
        return pd.DataFrame()
    try:
        sina_codes = []
        for c in codes:
            c = c.strip()
            if c.startswith(("sh", "sz", "bj")):
                sina_codes.append(c)
            elif c.startswith(("6", "5")):
                sina_codes.append(f"sh{c}")
            elif c.startswith(("0", "3", "2")):
                sina_codes.append(f"sz{c}")
            else:
                sina_codes.append(c)

        url = SINA_URL + ",".join(sina_codes)
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = "gbk"
        text = resp.text

        rows = []
        for line in text.strip().split("\n"):
            match = re.search(r'"(.*)"', line)
            if not match:
                continue
            parts = match.group(1).split(",")
            if len(parts) < 30:
                continue
            try:
                row = {
                    "代码": parts[0].split("_")[-1] if "_" in parts[0] else parts[0],
                    "名称": parts[0],
                    "最新价": float(parts[1]) if parts[1] else 0,
                    "涨跌幅": 0,  # 新浪格式中需要额外计算
                    "昨收": float(parts[2]) if parts[2] else 0,
                    "开盘": float(parts[3]) if parts[3] else 0,
                    "最高": float(parts[4]) if parts[4] else 0,
                    "最低": float(parts[5]) if parts[5] else 0,
                    "成交量": float(parts[8]) if len(parts) > 8 and parts[8] else 0,
                    "成交额": float(parts[9]) if len(parts) > 9 and parts[9] else 0,
                }
                # 计算涨跌幅
                if row["最新价"] > 0 and row["昨收"] > 0:
                    row["涨跌幅"] = (row["最新价"] - row["昨收"]) / row["昨收"] * 100
                rows.append(row)
            except (ValueError, IndexError):
                continue

        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as e:
        logger.warning(f"新浪行情接口失败: {e}")
        return pd.DataFrame()


def get_quotes(codes: List[str]) -> pd.DataFrame:
    """获取实时行情（腾讯优先，新浪兜底）

    Args:
        codes: 股票代码列表

    Returns:
        DataFrame
    """
    df = _get_tencent_quotes(codes)
    if not df.empty:
        return df
    df = _get_sina_quotes(codes)
    if not df.empty:
        return df
    logger.error("所有行情接口均失败")
    return pd.DataFrame()


def get_realtime_quotes() -> pd.DataFrame:
    """获取全市场实时行情（使用腾讯接口）"""
    try:
        # 腾讯批量接口获取全市场行情
        import akshare as ak
        # 先用 akshare 获取股票列表
        df_list = ak.stock_zh_a_spot()
        if not df_list.empty:
            codes = df_list["代码"].tolist()[:100]  # 取前100只
            return get_quotes(codes)
        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"全市场行情失败: {e}")
        return pd.DataFrame()


def get_tencent_pe_pb(code: str) -> dict:
    """获取单只股票的 PE/PB/市值（腾讯接口）

    Args:
        code: 6位股票代码，如 "600519"

    Returns:
        dict with keys: pe, pb, market_cap (亿元)
    """
    df = _get_tencent_quotes([code])
    if df.empty:
        return {}
    row = df.iloc[0]
    return {
        "pe": row.get("市盈率") if row.get("市盈率", 0) > 0 else None,
        "pb": row.get("市净率"),
        "market_cap": row.get("总市值"),
    }
