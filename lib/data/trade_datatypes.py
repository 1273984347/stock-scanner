"""Broker 数据类：Order / Trade / Holding（评审 P1-6 V1 下沉，2026-11-20）

原 lib/risk/datatypes.py —— data.broker 顶层 import lib.risk 构成全库唯一的
「低层→高层」包级环（V1）。纯数据类下沉到更低层 data 包后环解除：
lib.risk 聚合出口（__init__）保留 ``from lib.risk import Order`` 兼容。
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Order:
    """订单"""
    code: str
    direction: str  # BUY / SELL
    price: float
    shares: int
    date: str
    reason: str = ""


@dataclass
class Trade:
    """成交"""
    code: str
    direction: str
    price: float
    shares: int
    amount: float
    commission: float
    stamp_tax: float
    impact_cost: float = 0.0
    date: str = ""
    reason: str = ""


@dataclass
class Holding:
    """持仓明细"""
    code: str
    shares: int
    cost_price: float
    buy_date: str
    can_sell_date: str  # T+1 后可卖日期
