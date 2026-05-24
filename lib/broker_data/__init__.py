"""Broker 数据类拆分包

本包从 lib/broker.py 拆分出来，实现单一职责原则：
- datatypes: Order/Trade/Holding 数据类
- impact: ImpactConfig/MarketImpactModel 冲击成本模型
- constraints: TradingConstraints 交易约束

导入方式：
- from lib.broker_data import Order, Trade, Holding
- from lib.broker_data import ImpactConfig, TradingConstraints
"""
from lib.broker_data.datatypes import Order, Trade, Holding
from lib.broker_data.impact import ImpactConfig, MarketImpactModel, get_impact_config_by_market_cap
from lib.broker_data.constraints import TradingConstraints

__all__ = [
    # 数据类
    "Order",
    "Trade",
    "Holding",
    # 冲击成本
    "ImpactConfig",
    "MarketImpactModel",
    "get_impact_config_by_market_cap",
    # 交易约束
    "TradingConstraints",
]
