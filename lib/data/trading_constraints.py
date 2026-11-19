"""Broker 交易约束：TradingConstraints"""
import pandas as pd
from typing import Dict, List, Tuple, Optional


class TradingConstraints:
    """A 股交易限制集中管理

    统一管理所有交易限制规则，供回测引擎和策略层调用。
    """

    def __init__(self, lot_size: int = 100, max_single_weight: float = 0.10,
                 exclude_st: bool = True, exclude_new_stocks: bool = True,
                 new_stock_days: int = 60):
        self.lot_size = lot_size
        self.max_single_weight = max_single_weight
        self.exclude_st = exclude_st
        self.exclude_new_stocks = exclude_new_stocks
        self.new_stock_days = new_stock_days

    def is_st_stock(self, name: str) -> bool:
        """判断是否为 ST 股票"""
        if not name or not isinstance(name, str):
            return False
        return "ST" in name.upper()

    def is_new_stock(self, code: str, market_data: Dict[str, pd.DataFrame],
                     date: str) -> bool:
        """判断是否为次新股（上市不足 N 天）"""
        df = market_data.get(code)
        if df is None or df.empty:
            return True

        target = pd.Timestamp(date)
        first_date = df["trade_date"].min()
        if pd.isna(first_date):
            return True

        days_since_listed = (target - first_date).days
        return days_since_listed < self.new_stock_days

    def adjust_shares_to_lot(self, shares: int) -> int:
        """调整为整手数（向下取整）"""
        return (shares // self.lot_size) * self.lot_size

    def can_buy(self, code: str, market_data: Dict[str, pd.DataFrame],
                date: str, stock_names: Dict[str, str] = None) -> Tuple[bool, str]:
        """检查是否可以买入"""
        target_date = pd.Timestamp(date)
        df = market_data.get(code)
        if df is None or df.empty:
            return (False, "无行情数据")

        latest = df[df["trade_date"] <= target_date]
        if latest.empty:
            return (False, "无当日行情")

        row = latest.iloc[-1]

        if row.get("is_suspend", False):
            return (False, "停牌")

        if row.get("is_limit_up", False):
            return (False, "涨停无法买入")

        if self.exclude_st and stock_names:
            name = stock_names.get(code, "")
            if self.is_st_stock(name):
                return (False, "ST股票")

        if self.exclude_new_stocks and self.is_new_stock(code, market_data, date):
            return (False, f"次新股（上市不足{self.new_stock_days}天）")

        return (True, "")

    def can_sell(self, code: str, market_data: Dict[str, pd.DataFrame],
                 date: str, holding=None) -> Tuple[bool, str]:
        """检查是否可以卖出"""
        target_date = pd.Timestamp(date)
        df = market_data.get(code)
        if df is None or df.empty:
            return (False, "无行情数据")

        latest = df[df["trade_date"] <= target_date]
        if latest.empty:
            return (False, "无当日行情")

        row = latest.iloc[-1]

        if row.get("is_suspend", False):
            return (False, "停牌")

        if row.get("is_limit_down", False):
            return (False, "跌停无法卖出")

        if holding and holding.can_sell_date > date:
            return (False, "T+1限制")

        return (True, "")

    def compute_buy_shares(self, available_cash: float, price: float,
                           max_weight: float = None) -> int:
        """计算可买入股数（考虑手数限制）

        Args:
            available_cash: 可用资金
            price: 买入价格
            max_weight: 最大仓位权重（None=不限制）

        Returns:
            可买入股数（整手）
        """
        if price <= 0 or available_cash <= 0:
            return 0

        max_amount = available_cash
        if max_weight is not None:
            max_amount = min(max_amount, available_cash * max_weight)

        raw_shares = int(max_amount / price)
        return self.adjust_shares_to_lot(raw_shares)

    def compute_sell_shares(self, holding_shares: int, target_shares: int = 0) -> int:
        """计算可卖出股数（考虑手数限制）

        Args:
            holding_shares: 当前持仓
            target_shares: 目标持仓（0=全部卖出）

        Returns:
            可卖出股数（清仓时允许零股，部分卖出时整手）
        """
        if target_shares >= holding_shares:
            # 清仓：A 股允许零股卖出
            return holding_shares

        diff = holding_shares - target_shares
        return self.adjust_shares_to_lot(diff)

    def get_tradable_universe(self, codes: List[str],
                              market_data: Dict[str, pd.DataFrame],
                              date: str,
                              stock_names: Dict[str, str] = None) -> List[str]:
        """获取当日可交易股票池（剔除停牌/ST/次新/涨跌停）

        这是回测上下文的核心方法，防止未来信息泄漏。
        """
        tradable = []
        for code in codes:
            can, _ = self.can_buy(code, market_data, date, stock_names)
            if can:
                tradable.append(code)
        return tradable
