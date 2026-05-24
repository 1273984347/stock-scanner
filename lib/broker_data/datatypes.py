"""Broker 数据类：Order / Trade / Holding"""
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
