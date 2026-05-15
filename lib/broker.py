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
import logging

import akshare as ak

logger = logging.getLogger(__name__)


@dataclass
class ImpactConfig:
    """冲击成本模型配置

    冲击成本公式（Almgren-Chriss 2005）：
    1. 平方根模型：impact(bps) = η * σ_daily * sqrt(Q/ADV) * 10000
       - η (eta): 系数，默认 0.1。值越大表示冲击成本对交易量的敏感度越高
       - σ_daily: 日波动率
       - Q: 交易股数，ADV: 日均成交量

    2. 线性模型：impact(bps) = λ * (Q/ADV) * 10000
       - λ (lambda_): 系数，默认 5.0

    调参建议：
    - 高流动性大盘股：η=0.05~0.1, λ=3~5
    - 中小盘股：η=0.1~0.2, λ=5~10
    - 极端市场（涨跌停附近）：η=0.2~0.5
    """
    model: str = "square_root"  # square_root / linear / none
    eta: float = 0.1  # 平方根模型系数（bps 基准）
    lambda_: float = 5.0  # 线性模型系数（bps）
    volume_window: int = 20  # 日均成交量计算窗口
    min_volume_ratio: float = 0.01  # 最小参与率（低于此值不计冲击）
    max_impact_bps: float = 50.0  # 最大冲击成本上限（bps）
    use_volatility_scaling: bool = True  # 是否用波动率缩放


def get_impact_config_by_market_cap(market_cap: float) -> ImpactConfig:
    """根据市值获取对应的冲击成本配置

    Args:
        market_cap: 流通市值（元）

    Returns:
        适合该市值分组的 ImpactConfig

    市值分组标准（参考 A 股市场）：
    - 大盘股：流通市值 > 500亿（> 5e10）
    - 中盘股：流通市值 100~500亿（1e10 ~ 5e10）
    - 小盘股：流通市值 < 100亿（< 1e10）
    """
    if market_cap is None or market_cap <= 0:
        # 未知市值使用中档参数
        return ImpactConfig(model="square_root", eta=0.15, lambda_=7.0,
                          volume_window=20, max_impact_bps=50.0)
    elif market_cap > 5e10:  # 大盘股 > 500亿
        return ImpactConfig(model="square_root", eta=0.07, lambda_=4.0,
                          volume_window=20, max_impact_bps=30.0)
    elif market_cap > 1e10:  # 中盘股 100-500亿
        return ImpactConfig(model="square_root", eta=0.12, lambda_=6.0,
                          volume_window=20, max_impact_bps=40.0)
    else:  # 小盘股 < 100亿
        return ImpactConfig(model="square_root", eta=0.18, lambda_=9.0,
                          volume_window=20, max_impact_bps=60.0)


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
            sigma = volatility if (volatility is not None and self.config.use_volatility_scaling) else 0.02
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
    reason: str = ""


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
                 stamp_tax_rate: float = 0.0005,  # 0.05%（2023-08-28 调整后）
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
        self._default_impact_config = impact_config
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
        self._stock_names: Dict[str, str] = {}  # 回测场景下的股票名称映射
        self._market_cap_cache: Dict[str, float] = {}  # 市值缓存

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

    def set_stock_names(self, names: Dict[str, str]):
        """设置股票名称映射（回测场景下 market_data 无名称列时使用）"""
        self._stock_names = names

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

    @staticmethod
    def get_limit_pct(code: str, name: str = "") -> float:
        """根据股票代码和名称判断涨跌停幅度

        A 股规则：
        - ST/*ST: 5%
        - 主板（60/00 开头）: 10%
        - 创业板（30 开头）: 20%
        - 科创板（68 开头）: 20%
        - 北交所（8/4 开头）: 30%
        """
        if name and isinstance(name, str) and "ST" in name.upper():
            return 0.05
        if code.startswith("30") or code.startswith("68"):
            return 0.20
        if code.startswith("8") or code.startswith("4"):
            return 0.30
        return 0.10

    def _get_market_cap(self, code: str, market_data: Dict[str, pd.DataFrame], date: str) -> float:
        """获取股票流通市值

        Args:
            code: 股票代码
            market_data: 行情数据
            date: 日期

        Returns:
            流通市值（元），未知返回 -1
        """
        if code in self._market_cap_cache:
            return self._market_cap_cache[code]
        df = market_data.get(code)
        if df is None or df.empty:
            return -1
        target = pd.Timestamp(date)
        hist = df[df["trade_date"] <= target]
        if hist.empty:
            return -1
        latest = hist.iloc[-1]
        close = latest.get("close", 0)
        volume = latest.get("volume", 0)
        # 流通市值 = 收盘价 × 流通股本
        if close > 0:
            # 优先使用 akshare 获取实际流通股本
            try:
                info_df = ak.stock_individual_info_em(symbol=code)
                if info_df is not None and not info_df.empty:
                    # 查找流通股本字段（可能有不同命名）
                    cap_row = info_df[info_df["item"].str.contains("流通", na=False)]
                    if not cap_row.empty:
                        cap_str = cap_row.iloc[0]["value"]
                        # 处理格式：e.g., "4.56亿股" 或 "456000000"
                        if "亿" in cap_str:
                            market_cap = float(cap_str.replace("亿股", "")) * 1e8 * close
                        elif "万" in cap_str:
                            market_cap = float(cap_str.replace("万股", "")) * 1e4 * close
                        else:
                            market_cap = float(cap_str) * close
                        self._market_cap_cache[code] = market_cap
                        return market_cap
            except Exception:
                pass

            # 降级方案：使用成交量估算（误差大，但不至于完全失效）
            if volume > 0:
                market_cap = close * volume * 100  # 假设 1 手=100股
                self._market_cap_cache[code] = market_cap
                return market_cap
        return -1

    def _get_dynamic_impact_model(self, code: str, market_data: Dict[str, pd.DataFrame],
                                  date: str) -> MarketImpactModel:
        """获取基于市值的动态冲击成本模型"""
        market_cap = self._get_market_cap(code, market_data, date)
        if market_cap <= 0:
            return self.impact_model  # 使用默认配置
        dynamic_config = get_impact_config_by_market_cap(market_cap)
        return MarketImpactModel(dynamic_config)

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
        else:
            # A 股买入必须整手
            if order.shares % self.lot_size != 0:
                order.shares = (order.shares // self.lot_size) * self.lot_size
                if order.shares <= 0:
                    return None

        # 获取实际成交价（含滑点 + 冲击成本）
        df = market_data.get(order.code)
        latest = df[df["trade_date"] <= pd.Timestamp(date)].iloc[-1]
        base_price = latest["close"]

        daily_volume = self._compute_daily_volume(order.code, market_data, date)
        volatility = self._compute_volatility(order.code, market_data, date)

        # 按板块确定涨跌停幅度
        stock_name = self._stock_names.get(order.code,
                      latest.get("名称", latest.get("name", "")))
        limit_pct = self.get_limit_pct(order.code, stock_name)

        if order.direction == "BUY":
            slippage_price = base_price * (1 + self.slippage)
            impact_model = self._get_dynamic_impact_model(order.code, market_data, date)
            impact_price = impact_model.estimate_impact_price(
                order.shares, base_price, daily_volume, "BUY", volatility
            )
            exec_price = max(slippage_price, impact_price)
            limit_up = latest["pre_close"] * (1 + limit_pct)
            exec_price = min(exec_price, limit_up)
        else:
            slippage_price = base_price * (1 - self.slippage)
            impact_model = self._get_dynamic_impact_model(order.code, market_data, date)
            impact_price = impact_model.estimate_impact_price(
                order.shares, base_price, daily_volume, "SELL", volatility
            )
            exec_price = min(slippage_price, impact_price)
            limit_down = latest["pre_close"] * (1 - limit_pct)
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
            next_sell_date = self._next_trade_date(date, market_data)
            if order.code in self.holdings:
                h = self.holdings[order.code]
                total_cost_basis = h.shares * h.cost_price + amount
                h.shares += order.shares
                h.cost_price = total_cost_basis / h.shares if h.shares > 0 else 0
                # T+1: 加仓部分需要更新 can_sell_date
                # 仅当旧仓已可卖时才更新（旧仓今日可卖则整体锁定到明日）
                if date >= h.can_sell_date:
                    h.can_sell_date = next_sell_date
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
            date=date, reason=order.reason
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


# ========== 实盘券商接口 ==========

class LiveBrokerABC:
    """实盘券商抽象接口

    定义实盘交易的通用接口，子类实现具体券商的连接逻辑。

    使用方式：
    1. 自定义子类实现抽象方法
    2. 实例化后传入 PositionManager 或直接使用
    3. 支持回测/模拟/实盘切换

    示例：
        class MyBroker(LiveBrokerABC):
            def _connect(self): ...
            def _place_order(self, order): ...
            ...

        broker = MyBroker(account="xxx", token="yyy")
        broker.submit_order(order)
    """

    def __init__(self, account: str, token: str = "", env: str = "paper"):
        """
        Args:
            account: 账户ID
            token: API Token / 密码
            env: 运行环境 - paper(模拟) / simulation(回测) / live(实盘)
        """
        self.account = account
        self.token = token
        self.env = env  # paper / simulation / live
        self._connected = False
        self._positions: Dict[str, dict] = {}
        self._orders: List[dict] = []

    def _connect(self):
        """建立连接（子类实现）"""
        raise NotImplementedError

    def _disconnect(self):
        """断开连接（子类实现）"""
        raise NotImplementedError

    def _place_order(self, order: Order) -> Optional[Trade]:
        """下单（子类实现）"""
        raise NotImplementedError

    def _cancel_order(self, order_id: str) -> bool:
        """撤单（子类实现）"""
        raise NotImplementedError

    def _query_positions(self) -> Dict[str, dict]:
        """查询持仓（子类实现）"""
        raise NotImplementedError

    def _query_balance(self) -> dict:
        """查询账户余额（子类实现）"""
        raise NotImplementedError

    def connect(self) -> bool:
        """建立连接"""
        try:
            self._connect()
            self._connected = True
            logger.info(f"实盘券商连接成功: {self.account} [{self.env}]")
            return True
        except Exception as e:
            logger.error(f"实盘券商连接失败: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """断开连接"""
        try:
            self._disconnect()
        except Exception:
            pass
        self._connected = False
        logger.info("实盘券商已断开")

    def submit_order(self, order: Order, market_data: Dict[str, pd.DataFrame] = None) -> Optional[Trade]:
        """提交订单"""
        if not self._connected:
            logger.error("未连接券商")
            return None
        try:
            return self._place_order(order)
        except Exception as e:
            logger.error(f"下单失败: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        if not self._connected:
            return False
        return self._cancel_order(order_id)

    def get_positions(self) -> Dict[str, dict]:
        """获取当前持仓"""
        if not self._connected:
            return {}
        try:
            self._positions = self._query_positions()
            return self._positions
        except Exception as e:
            logger.error(f"查询持仓失败: {e}")
            return {}

    def get_balance(self) -> dict:
        """获取账户余额"""
        if not self._connected:
            return {}
        return self._query_balance()

    @property
    def is_connected(self) -> bool:
        return self._connected


class PaperBroker(LiveBrokerABC):
    """模拟实盘券商（纸带交易）

    使用真实市场数据进行模拟交易，适合实盘前的策略验证。

    特点：
    - 使用实时行情数据（通过 data_layer 获取）
    - 严格遵循 A 股交易规则（T+1、涨跌停等）
    - 记录完整交易历史
    - 支持与 ABroker 互换使用
    """

    def __init__(self, account: str = "paper", initial_cash: float = 1_000_000, **kwargs):
        super().__init__(account=account, env="paper")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self._broker = ABroker(initial_cash=initial_cash)
        self._market_data: Dict[str, pd.DataFrame] = {}

    def _connect(self):
        """PaperBroker 连接即初始化本地代理"""
        from lib.data_layer import get_realtime_quotes
        quotes = get_realtime_quotes()
        logger.info(f"PaperBroker 初始化，行情条目: {len(quotes)}")

    def _disconnect(self):
        """无实际连接需要断开"""
        pass

    def _place_order(self, order: Order) -> Optional[Trade]:
        """通过本地 ABroker 执行订单"""
        if not self._market_data:
            logger.warning("无市场数据，使用实时行情")
            from lib.data_layer import get_stock_hist_multi_source
            df = get_stock_hist_multi_source(order.code, days=5, source="auto")
            if not df.empty:
                df = df.rename(columns={"date": "trade_date"})
                self._market_data[order.code] = df

        if order.code in self._market_data:
            trade = self._broker.submit_order(
                order, self._market_data, order.date
            )
            if trade:
                self._orders.append({
                    "order_id": f"paper_{len(self._orders)}",
                    "trade": trade,
                    "status": "filled"
                })
            return trade
        return None

    def _cancel_order(self, order_id: str) -> bool:
        """撤销订单（PaperBroker 不支持挂单）"""
        return False

    def _query_positions(self) -> Dict[str, dict]:
        """查询持仓"""
        positions = {}
        for code, holding in self._broker.holdings.items():
            positions[code] = {
                "code": code,
                "name": getattr(holding, 'name', code),
                "shares": holding.shares,
                "cost_price": holding.cost_price,
                "buy_date": holding.buy_date,
                "market_value": holding.shares * self._broker._last_prices.get(code, holding.cost_price),
            }
        return positions

    def _query_balance(self) -> dict:
        """查询余额"""
        return {
            "cash": self.cash,
            "market_value": self._broker.total_market_value,
            "total_value": self._broker.total_value,
            "initial_cash": self.initial_cash,
        }

    def update_market_data(self, code: str, data: pd.DataFrame):
        """更新市场数据（外部注入）"""
        if "date" in data.columns and "trade_date" not in data.columns:
            data = data.rename(columns={"date": "trade_date"})
        self._market_data[code] = data

    def set_trade_dates(self, dates: list):
        """设置交易日历"""
        self._broker.set_trade_dates(dates)

    def get_performance_df(self) -> pd.DataFrame:
        """获取净值曲线"""
        return self._broker.get_performance_df()

    def get_trade_history(self) -> List[Trade]:
        """获取交易历史"""
        return self._broker.trade_history

    @property
    def total_value(self) -> float:
        return self._broker.total_value

    @property
    def holdings(self):
        return self._broker.holdings