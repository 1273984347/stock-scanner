"""风险与执行层 —— 风控/仓位/约束/成本/滑点/成交率。

交易数据类型已下沉到 lib/data（评审 P1-6 V1，2026-11-20）：data.broker 顶层
import lib.risk 构成唯一的低层→高层包级环。本包 __init__ 保留聚合出口
（``from lib.risk import Order`` 等用法兼容，backtest/tests 在用）。
"""
from lib.data.trade_datatypes import Order, Trade, Holding
from lib.data.market_impact import ImpactConfig, MarketImpactModel, get_impact_config_by_market_cap
from lib.data.trading_constraints import TradingConstraints

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
