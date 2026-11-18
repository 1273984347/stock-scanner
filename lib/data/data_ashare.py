"""Ashare 兼容层 —— 腾讯行情委托（唯一解析在 ``data_realtime``）。

来源：mpquant/Ashare（GitHub 3.5k stars）的双内核思路（新浪 + 腾讯）。
本文件的解析实现已于 2026-11-18（ADR-0024）全部删除：
- 腾讯/新浪报文解析只允许存在于 ``lib/data/data_realtime.py``；
  本文件保留同名函数仅为兼容既有调用点（``data_fund_flow`` / ``data_layer``）。
- 旧的 ``get_realtime_quotes()`` 已删除：它先调 akshare 取代码表再**只跑前 100 只**，
  是半成品（全市场被静默截断到 100 只），且无人调用。
"""
import logging
from typing import List

import pandas as pd

logger = logging.getLogger(__name__)


def _get_tencent_quotes(codes: List[str]) -> pd.DataFrame:
    """腾讯批量行情（兼容名）。单位已归一为 股/元，见 data_realtime。

    历史缺陷：旧实现直接透传腾讯的「手 / 万元」，与 ``get_stock_hist`` 的「股」
    相差 100 倍，导致资金流腾讯估算分支 ``vol_ratio > 1.5`` 几乎永不成立。
    """
    from lib.data.data_realtime import fetch_tencent_quotes
    return fetch_tencent_quotes(codes)


def get_quotes(codes: List[str]) -> pd.DataFrame:
    """腾讯优先的批量行情（兼容接口，等价 ``fetch_tencent_quotes``）。"""
    return _get_tencent_quotes(codes)


def get_tencent_pe_pb(code: str) -> dict:
    """获取单只股票的 PE/PB/市值（腾讯接口）。

    投影逻辑复用 ``data_tencent.get_tencent_quote``（同一份，不重复实现）。

    Args:
        code: 6位股票代码，如 "600519"

    Returns:
        dict with keys: pe, pb, market_cap (亿元)；未取到返回空 dict
    """
    from lib.data.data_tencent import get_tencent_quote
    return get_tencent_quote(code)
