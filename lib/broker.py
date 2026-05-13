"""
A 股模拟券商 —— 严格模拟 A 股交易规则
- T+1 制度（当日买入次日才能卖出）
- 涨跌停限制（买不到/卖不掉）
- 停牌处理
- 交易成本（佣金+印花税+滑点+冲击成本）
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class ImpactConfig:
    """冲击成本模型配置"""
    model: str = "square_root"  # square_root / linear / none
    eta: float = 0.1  # 平方根模型系数（bps 基准）
    lambda_: float = 5.0  # 线性模型系数（bps）
    volume_window: int = 20  # 日均成交量计算窗口
    min_volume_ratio: float = 0.01  # 最小参与率（低于此值不计冲击）
    max_impact_bps: float = 50.0  # 最大冲击成本上限（bps）
    use_volatility_scaling: bool = True  # 是否用波动率缩放


class MarketImpactModel:
    """市场冲击成本模型

    支持两种模型：
    1. 平方根模型（Almgren-Chriss 经典）：
       impact(bps) = η * σ_daily * sqrt(Q / ADV)
       其中 σ_daily = 日波动率，Q = 交易量，ADV = 日均成交量

    2. 线性模型：
       impact(bps) = λ * (Q / ADV) * 10000

    参考：Almgren, Thum, Hauptmann, Li (2005)
    """

    def __init__(self, config: ImpactConfig = None):
        self.config = config or ImpactConfig()

    def estimate_impact_bps(self, shares: int, price: float,
                            daily_volume: float, volatility: float = None) -> float:
        """估算冲击成本（bps）

        Args:
            shares: 交易股数
            price: 当前价格
            daily_volume: 日均成交量（股）
            volatility: 日波动率（标准差，可选）

        Returns:
            冲击成本（bps）
        """
        if self.config.model == "none":
            return 0.0

        if daily_volume <= 0 or shares <= 0:
            return 0.0

        participation_rate = shares / daily_volume

        if participation_rate < self.config.min_volume_ratio:
            return 0.0

        if self.config.model == "linear":
            impact = self.config.lambda_ * participation_rate * 10000
        elif self.config.model == "square_root":
            sigma = volatility if (volatility and self.config.use_volatility_scaling) else 0.02
            impact = self.config.eta * sigma * np.sqrt(participation_rate) * 10000
        else:
            return 0.0

        return min(impact, self.config.max_impact_bps)

    def estimate_impact_price(self, shares: int, base_price: float,
                              daily_volume: float, direction: str = "BUY",
                              volatility: float = None) -> float:
        """估算冲击调整后的成交价

        Args:
            shares: 交易股数
            base_price: 基准价格（收盘价）
            daily_volume: 日均成交量
            direction: BUY / SELL
            volatility: 日波动率

        Returns:
            冲击调整后的成交价
        """
        impact_bps = self.estimate_impact_bps(shares, base_price, daily_volume, volatility)
        impact_pct = impact_bps / 10000.0

        if direction == "BUY":
            return base_price * (1 + impact_pct)
        else:
            return base_price * (1 - impact_pct)


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


@dataclass
class Holding:
    """持仓明细"""
    code: str
    shares: int
    cost_price: float
    buy_date: str
    can_sell_date: str  # T+1 后可卖日期


class ABroker:
    """A 股模拟券商"""

    def __init__(self, initial_cash: float = 1_000_000,
                 commission_rate: float = 0.00025,
                 stamp_tax_rate: float = 0.001,
                 min_commission: float = 5.0,
                 slippage: float = 0.001,
                 lot_size: int = 100,
                 impact_config: ImpactConfig = None):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission_rate = commission_rate
        self.stamp_tax_rate = stamp_tax_rate
        self.min_commission = min_commission
        self.slippage = slippage
        self.lot_size = lot_size
        self.impact_model = MarketImpactModel(impact_config)

        self.holdings: Dict[str, Holding] = {}
        self.trade_history: list = []
        self.daily_values: list = []
        self._last_prices: Dict[str, float] = {}
        self._missing_days: Dict[str, int] = {}
        self._delisted_codes: set = set()
        self._trade_dates: list = []
        self._trade_date_index: Dict[str, int] = {}
        self._volume_cache: Dict[str, float] = {}

    @property
    def total_market_value(self) -> float:
        return sum(
            h.shares * self._last_prices.get(h.code, h.cost_price)
            for h in self.holdings.values()
        )

    @property
    def total_value(self) -> float:
        return self.cash + self.total_market_value

    @property
    def position_count(self) -> int:
        return len(self.holdings)

    def update_market_prices(self, market_data: Dict[str, pd.DataFrame], date: str):
        """更新市场价，同时检测退市"""
        target_date = pd.Timestamp(date)
        self._last_prices = {}
        for code in list(self.holdings.keys()):
            df = market_data.get(code)
            if df is not None and not df.empty:
                latest = df[df["trade_date"] <= target_date]
                if not latest.empty:
                    self._last_prices[code] = latest["close"].iloc[-1]
                    self._missing_days[code] = 0
                    continue
            self._missing_days[code] = self._missing_days.get(code, 0) + 1
            if self._missing_days[code] >= 60:
                if code not in self._delisted_codes:
                    self._delisted_codes.add(code)
                    logger.warning(f"{date} {code} 连续 {self._missing_days[code]} 天无行情，疑似退市")

    def get_delisted_codes(self) -> set:
        """获取疑似退市的股票代码"""
        return self._delisted_codes

    def can_trade(self, code: str, market_data: Dict[str, pd.DataFrame],
                  date: str) -> Tuple[bool, str]:
        """检查是否可以交易"""
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

        if row.get("is_limit_down", False):
            return (False, "跌停无法卖出")

        return (True, "")

    def submit_order(self, order: Order, market_data: Dict[str, pd.DataFrame],
                     date: str) -> Optional[Trade]:
        """提交订单，返回成交（或 None）"""
        can, reason = self.can_trade(order.code, market_data, date)
        if not can:
            logger.debug(f"{date} {order.code} {order.direction} 无法交易: {reason}")
            return None

        # T+1 检查：当日买入的不能卖出
        if order.direction == "SELL":
            holding = self.holdings.get(order.code)
            if holding is None:
                return None
            if holding.can_sell_date > date:
                logger.debug(f"{date} {order.code} T+1 限制，不可卖出")
                return None
            if holding.shares < order.shares:
                order.shares = holding.shares

        # 获取实际成交价（含滑点 + 冲击成本）
        df = market_data.get(order.code)
        latest = df[df["trade_date"] <= pd.Timestamp(date)].iloc[-1]
        base_price = latest["close"]

        daily_volume = self._compute_daily_volume(order.code, market_data, date)
        volatility = self._compute_volatility(order.code, market_data, date)

        if order.direction == "BUY":
            slippage_price = base_price * (1 + self.slippage)
            impact_price = self.impact_model.estimate_impact_price(
                order.shares, base_price, daily_volume, "BUY", volatility
            )
            exec_price = max(slippage_price, impact_price)
            limit_up = latest["pre_close"] * 1.10
            exec_price = min(exec_price, limit_up)
        else:
            slippage_price = base_price * (1 - self.slippage)
            impact_price = self.impact_model.estimate_impact_price(
                order.shares, base_price, daily_volume, "SELL", volatility
            )
            exec_price = min(slippage_price, impact_price)
            limit_down = latest["pre_close"] * 0.90
            exec_price = max(exec_price, limit_down)

        impact_cost = abs(exec_price - slippage_price) * order.shares

        # 资金检查
        amount = exec_price * order.shares
        commission = max(amount * self.commission_rate, self.min_commission)
        stamp_tax = amount * self.stamp_tax_rate if order.direction == "SELL" else 0
        total_cost = amount + commission + stamp_tax

        if order.direction == "BUY" and total_cost > self.cash:
            max_shares = int(self.cash / (exec_price * (1 + self.commission_rate)) / self.lot_size) * self.lot_size
            if max_shares < self.lot_size:
                return None
            order.shares = max_shares
            amount = exec_price * order.shares
            commission = max(amount * self.commission_rate, self.min_commission)
            total_cost = amount + commission

        # 执行交易
        if order.direction == "BUY":
            self.cash -= total_cost
            if order.code in self.holdings:
                h = self.holdings[order.code]
                total_cost_basis = h.shares * h.cost_price + amount
                h.shares += order.shares
                h.cost_price = total_cost_basis / h.shares if h.shares > 0 else 0
            else:
                self.holdings[order.code] = Holding(
                    code=order.code, shares=order.shares,
                    cost_price=exec_price, buy_date=date,
                    can_sell_date=self._next_trade_date(date, market_data)
                )
        else:
            self.cash += amount - commission - stamp_tax
            h = self.holdings[order.code]
            h.shares -= order.shares
            if h.shares <= 0:
                del self.holdings[order.code]

        trade = Trade(
            code=order.code, direction=order.direction,
            price=exec_price, shares=order.shares,
            amount=amount, commission=commission,
            stamp_tax=stamp_tax, impact_cost=impact_cost,
            date=date
        )
        self.trade_history.append(trade)
        return trade

    def set_trade_dates(self, dates: list):
        """预计算交易日列表（由引擎在数据准备后调用一次）"""
        self._trade_dates = sorted(dates)
        self._trade_date_index = {d: i for i, d in enumerate(self._trade_dates)}

    def _next_trade_date(self, date: str, market_data=None) -> str:
        """计算 T+1 可卖日期（O(1) 查找）

        若当前日期为最后一个交易日，返回一个远未来日期，
        确保当日买入的股票无法在回测期内卖出，严格模拟 T+1。
        """
        idx = self._trade_date_index.get(date)
        if idx is not None and idx + 1 < len(self._trade_dates):
            return self._trade_dates[idx + 1]
        return "9999-12-31"

    def _compute_daily_volume(self, code: str, market_data: Dict[str, pd.DataFrame],
                              date: str) -> float:
        """计算日均成交量（用于冲击成本估算）

        Args:
            code: 股票代码
            market_data: 行情数据
            date: 当前日期

        Returns:
            日均成交量（股）
        """
        cache_key = f"{code}_{date}"
        if cache_key in self._volume_cache:
            return self._volume_cache[cache_key]

        df = market_data.get(code)
        if df is None or df.empty:
            return 0.0

        target = pd.Timestamp(date)
        hist = df[df["trade_date"] <= target]
        if hist.empty:
            return 0.0

        window = self.impact_model.config.volume_window
        recent = hist.tail(window)
        if "volume" in recent.columns:
            adv = recent["volume"].mean()
        else:
            adv = 0.0

        self._volume_cache[cache_key] = adv
        return adv

    def _compute_volatility(self, code: str, market_data: Dict[str, pd.DataFrame],
                            date: str) -> float:
        """计算日波动率（用于冲击成本缩放）

        Args:
            code: 股票代码
            market_data: 行情数据
            date: 当前日期

        Returns:
            日收益率标准差
        """
        df = market_data.get(code)
        if df is None or df.empty:
            return 0.02

        target = pd.Timestamp(date)
        hist = df[df["trade_date"] <= target]
        if hist.empty or len(hist) < 20:
            return 0.02

        recent = hist.tail(60)
        if "close" in recent.columns:
            rets = recent["close"].pct_change().dropna()
            if len(rets) > 10:
                return float(rets.std())
        return 0.02

    def record_daily_snapshot(self, date: str):
        """记录每日净值"""
        self.daily_values.append({
            "trade_date": date,
            "cash": self.cash,
            "market_value": self.total_market_value,
            "total_value": self.total_value,
            "position_count": self.position_count,
        })

    def get_performance_df(self) -> pd.DataFrame:
        """获取净值序列"""
        df = pd.DataFrame(self.daily_values)
        if df.empty:
            return df
        df["daily_return"] = df["total_value"].pct_change()
        df["cumulative_return"] = df["total_value"] / self.initial_cash - 1
        return df

    def get_cost_summary(self) -> dict:
        """获取交易成本汇总"""
        total_commission = sum(t.commission for t in self.trade_history)
        total_stamp_tax = sum(t.stamp_tax for t in self.trade_history)
        total_impact = sum(t.impact_cost for t in self.trade_history)
        total_turnover = sum(t.amount for t in self.trade_history)
        return {
            "total_commission": total_commission,
            "total_stamp_tax": total_stamp_tax,
            "total_impact_cost": total_impact,
            "total_turnover": total_turnover,
            "total_cost": total_commission + total_stamp_tax + total_impact,
            "cost_ratio": (total_commission + total_stamp_tax + total_impact) / total_turnover
            if total_turnover > 0 else 0,
        }


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
        return "ST" in name.upper() or "*ST" in name.upper()

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
            可卖出股数（整手）
        """
        if target_shares >= holding_shares:
            return self.adjust_shares_to_lot(holding_shares)

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