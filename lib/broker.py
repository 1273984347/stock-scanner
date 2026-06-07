"""
A 股模拟券商 —— 严格模拟 A 股交易规则
- T+1 制度（当日买入次日才能卖出）
- 涨跌停限制（买不到/卖不掉）
- 停牌处理
- 交易成本（佣金+印花税+滑点+冲击成本）

本模块已拆分为 lib/broker_data/ 子包：
- lib.broker_data.datatypes: Order/Trade/Holding
- lib.broker_data.impact: ImpactConfig/MarketImpactModel
- lib.broker_data.constraints: TradingConstraints
- ABroker 核心券商仍在本文件
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import logging
import threading
from collections import OrderedDict

import akshare as ak

from config import (
    COMMISSION_RATE, STAMP_DUTY_RATE, MIN_COMMISSION, SLIPPAGE_RATE,
)

# 从拆分模块导入
from lib.broker_data.datatypes import Order, Trade, Holding
from lib.broker_data.impact import ImpactConfig, MarketImpactModel, get_impact_config_by_market_cap
from lib.broker_data.constraints import TradingConstraints

logger = logging.getLogger(__name__)


# 保留 LRUCache（被 data_layer 等模块使用）
class LRUCache:
    """线程安全的 LRU 缓存，支持标准字典访问语法"""
    def __init__(self, maxsize: int = 128):
        self.maxsize = maxsize
        self.cache = OrderedDict()
        self._lock = threading.Lock()
    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self.cache
    def __getitem__(self, key):
        with self._lock:
            if key not in self.cache:
                raise KeyError(key)
            self.cache.move_to_end(key)
            return self.cache[key]
    def __setitem__(self, key, value):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)
    def get(self, key, default=None):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return default
    def set(self, key, value):
        self[key] = value


class ABroker:
    """A 股模拟券商"""

    def __init__(self, initial_cash: float = 1_000_000,
                 commission_rate: float = COMMISSION_RATE,
                 stamp_tax_rate: float = STAMP_DUTY_RATE,
                 min_commission: float = MIN_COMMISSION,
                 slippage: float = SLIPPAGE_RATE,
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
        self._volume_cache = LRUCache(maxsize=1000)
        self._stock_names: Dict[str, str] = {}  # 回测场景下的股票名称映射
        self._market_cap_cache = LRUCache(maxsize=1000)  # 市值缓存
        self._broker_cache_lock = threading.Lock()  # 保护 _compute_daily_volume 等方法的缓存访问

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

    def last_price(self, code: str, default: float = np.nan) -> float:
        """安全获取最近一次更新的市价

        P1-1 修复（2026-06-07）：
        暴露显式 last_price() 方法，避免外部直接读 _last_prices 私有字段。
        若 update_market_prices 尚未调用或该 code 不在持仓中，
        返回 default（默认 NaN，调用方应显式处理）。

        Args:
            code: 股票代码
            default: 无价格时的默认值，默认 NaN

        Returns:
            最近一次市价，无则返回 default
        """
        with self._broker_cache_lock:
            return self._last_prices.get(code, default)

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
                with self._broker_cache_lock:
                    if code not in self._delisted_codes:
                        self._delisted_codes.add(code)
                        logger.warning(f"{date} {code} 连续 {self._missing_days[code]} 天无行情，疑似退市")

    def get_delisted_codes(self) -> set:
        """获取疑似退市的股票代码"""
        with self._broker_cache_lock:
            return self._delisted_codes.copy()

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
        with self._broker_cache_lock:
            if code in self._market_cap_cache:
                return self._market_cap_cache[code]
        df = market_data.get(code)
        if df is None or df.empty:
            # 缓存失败结果，避免重复查询
            with self._broker_cache_lock:
                self._market_cap_cache[code] = -1
            return -1
        target = pd.Timestamp(date)
        hist = df[df["trade_date"] <= target]
        if hist.empty:
            with self._broker_cache_lock:
                self._market_cap_cache[code] = -1
            return -1
        latest = hist.iloc[-1]
        close = latest.get("close", 0)
        volume = latest.get("volume", 0)
        # 流通市值 = 收盘价 × 流通股本
        market_cap_result = -1
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
                            market_cap_result = float(cap_str.replace("亿股", "")) * 1e8 * close
                        elif "万" in cap_str:
                            market_cap_result = float(cap_str.replace("万股", "")) * 1e4 * close
                        else:
                            market_cap_result = float(cap_str) * close
            except Exception as e:
                logger.warning(f"[broker] 流通市值获取失败: {e}")

            # 降级方案：使用成交量估算（误差大，标记为估算值）
            # 注：日成交量 ≠ 流通股本，此估算仅用于冲击成本的粗略参考
            if market_cap_result < 0 and volume > 0:
                # P0-2 修复（2026-06-05）：估算时返回 sentinel = -2
                # 区别于 -1（完全失败）和 >= 0（成功）
                # 让 _get_dynamic_impact_model 能区分并用最保守 impact_model
                # 同时打 warning log（不是 debug）让运维可见估算 fallback
                estimated_float_shares = volume / 0.03 if volume > 0 else 0
                # 仍可计算一个粗略市值用于指标，但 marker 让上层知道这是估算
                estimated_market_cap = close * estimated_float_shares
                # 返回 (-2, estimated_market_cap) 元组以保留估算值 + 标记
                market_cap_result = -2  # sentinel
                # 单独保存估算值供可能的 metric 输出
                if not hasattr(self, '_estimated_market_caps'):
                    self._estimated_market_caps = {}
                self._estimated_market_caps[code] = estimated_market_cap
                logger.warning(
                    f"[broker] {code} 流通市值获取失败，"
                    f"使用估算值 ¥{estimated_market_cap:,.0f}（基于成交量反推，误差 5-50x）"
                )
        # 缓存所有结果（包括失败结果-1），避免重复查询
        with self._broker_cache_lock:
            self._market_cap_cache[code] = market_cap_result
        return market_cap_result

    def _get_dynamic_impact_model(self, code: str, market_data: Dict[str, pd.DataFrame],
                                  date: str) -> MarketImpactModel:
        """获取基于市值的动态冲击成本模型

        P0-2 修复（2026-06-05）：
        - market_cap == -2 (估算 sentinel) → 用默认 impact_model（最保守）
        - market_cap == -1 (完全失败) → 用默认 impact_model
        - market_cap > 0 (成功) → 用动态 impact_model
        """
        market_cap = self._get_market_cap(code, market_data, date)
        if market_cap <= 0:
            # -2 (估算) 或 -1 (失败) 都用默认（最保守）
            return self.impact_model
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
            # A 股买入必须整手（P1-5 修复：floor 时打 warning）
            if order.shares % self.lot_size != 0:
                old_shares = order.shares
                order.shares = (order.shares // self.lot_size) * self.lot_size
                logger.warning(
                    f"{date} {order.code} 买入股数 {old_shares} 被向下取整到 {order.shares}（A 股 {self.lot_size} 股整手）"
                )
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
        with self._broker_cache_lock:
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

        with self._broker_cache_lock:
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
        except Exception as e:
            logger.debug(f"[broker] 断开连接异常: {e}")
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

    def __init__(self, account: str = "paper", initial_cash: float = 1_000_000,
                 max_single_weight: float = 0.20, max_total_exposure: float = 0.95,
                 stop_loss_pct: float = 0.05, **kwargs):
        super().__init__(account=account, env="paper")
        self.initial_cash = initial_cash
        # P0-1 修复（2026-06-07）：删除 self.cash = initial_cash
        # 历史 bug：PaperBroker 同时维护 self.cash（从未更新）和 self._broker.cash（被成交修改），
        # 导致 _query_balance() 永远返回初始资金，paper trading 决策失效。
        # 修复后现金单一来源：self._broker.cash
        self._broker = ABroker(initial_cash=initial_cash)
        self._market_data: Dict[str, pd.DataFrame] = {}
        self._max_single_weight = max_single_weight
        self._max_total_exposure = max_total_exposure
        self._stop_loss_pct = stop_loss_pct

    def _connect(self):
        """PaperBroker 连接即初始化本地代理"""
        from lib.data_layer import get_realtime_quotes
        quotes = get_realtime_quotes()
        logger.info(f"PaperBroker 初始化，行情条目: {len(quotes)}")

    def _disconnect(self):
        """无实际连接需要断开"""
        pass

    def _place_order(self, order: Order) -> Optional[Trade]:
        """通过本地 ABroker 执行订单（含风控检查）"""
        # 风控检查：买入时验证仓位限制
        if order.direction == "BUY":
            total_value = self._broker.total_value
            if total_value > 0:
                # 单票仓位限制
                existing = self._broker.holdings.get(order.code)
                # P1-1 修复：用 last_price() 显式拿价格，无值时退化为 cost_price
                # 旧版 _last_prices.get() 私有访问 + 无值时退化为 0 导致 first-buy 仓位检查失效
                if existing:
                    last_px = self._broker.last_price(existing.code, default=existing.cost_price)
                    existing_value = existing.shares * last_px
                else:
                    existing_value = 0
                new_value = existing_value + order.price * order.shares
                if new_value / total_value > self._max_single_weight:
                    logger.warning(f"PaperBroker 风控: {order.code} 仓位 {new_value/total_value:.1%} 超过 {self._max_single_weight:.0%} 上限")
                    # 缩减到限额内
                    max_new_value = total_value * self._max_single_weight - existing_value
                    if max_new_value <= 0:
                        return None
                    order.shares = int(max_new_value / order.price / 100) * 100
                    if order.shares <= 0:
                        return None

                # 总仓位限制
                total_market_value = self._broker.total_market_value
                if (total_market_value + order.price * order.shares) / total_value > self._max_total_exposure:
                    max_buy_value = total_value * self._max_total_exposure - total_market_value
                    if max_buy_value <= 0:
                        logger.warning("PaperBroker 风控: 总仓位已达上限")
                        return None
                    order.shares = int(max_buy_value / order.price / 100) * 100
                    if order.shares <= 0:
                        return None

        if not self._market_data:
            logger.warning("无市场数据，使用实时行情")
            from lib.data_layer import get_stock_hist_multi_source
            try:
                df = get_stock_hist_multi_source(order.code, days=5, source="auto")
            except Exception as e:
                logger.debug(f"[broker] 历史数据获取失败 [{order.code}]: {e}")
                df = pd.DataFrame()
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
        """查询余额

        P0-1 修复（2026-06-07）：cash 字段改读 self._broker.cash（单一来源），
        不再读已删除的 self.cash。修复后 _query_balance() 现金会随成交而变化。
        """
        return {
            "cash": self._broker.cash,
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


# ========== 通达信实盘券商 ==========

class TDXBroker(LiveBrokerABC):
    """通达信实盘券商

    通过 pytdx.trade 连接本机运行的通达信/同花顺客户端交易接口。

    前置条件：
    - 通达信客户端运行并开启交易 API（设置 → 个人中心 → 网络安全 → 允许网络写入）
    - 默认监听 http://127.0.0.1:10092/api

    交易接口参数：
    - category: 0=买入, 1=卖出
    - price_type: 0=限价, 1=市价（最新价）, 2=涨停价, 3=跌停价
    """

    def __init__(
        self,
        account: str,
        ip: str = "127.0.0.1",
        port: int = 10092,
        version: int = 8,
        yyb_id: str = "",
        trade_account: str = "",
        jy_password: str = "",
        tx_password: str = "",
    ):
        """
        Args:
            account:     资金账号
            ip:         通达信服务器 IP（默认本机）
            port:       通达信服务器端口（默认 10092）
            version:    通达信版本号（默认 8）
            yyb_id:     营业部 ID
            trade_account: 交易账号（一般同 account）
            jy_password: 交易密码
            tx_password: 通信密码
        """
        super().__init__(account=account, env="live")
        self._ip = ip
        self._port = port
        self._version = version
        self._yyb_id = yyb_id
        self._trade_account = trade_account or account
        self._jy_password = jy_password
        self._tx_password = tx_password
        self._api = None
        self._client_id: int = -1
        # 安全提示：密码在内存中明文存储，生产环境应使用密钥管理服务

    def _connect(self):
        from pytdx.trade import TdxTradeApi
        self._api = TdxTradeApi()
        self._api.heartbeat = True
        # 连接通达信交易服务器
        result = self._api.logon(
            ip=self._ip,
            port=self._port,
            version=self._version,
            yyb_id=self._yyb_id,
            account_id=self._account,
            trade_account=self._trade_account,
            jy_password=self._jy_password,
            tx_password=self._tx_password,
        )
        if result.get("success") == 1 or result.get("success") == True:
            self._client_id = result.get("client_id", 0)
        elif result.get("error") == -1:
            raise ConnectionError("通达信交易服务器连接失败，请确认客户端已开启交易API")
        else:
            raise ConnectionError(f"通达信登录失败: {result}")

    def _disconnect(self):
        if self._api and self._client_id >= 0:
            try:
                self._api.logoff(self._client_id)
            except Exception as e:
                logger.debug(f"[broker] TDX 登出异常: {e}")
        self._api = None
        self._client_id = -1

    def _place_order(self, order: Order) -> Optional[Trade]:
        # category: 0=买入, 1=卖出
        category = 0 if order.direction == "BUY" else 1
        # price_type: 0=限价
        price_type = 0
        # 交易所代码
        market = self._get_market(order.code)
        gddm = self._get_gddm(market)
        if gddm is None:
            logger.error(f"无法获取 {order.code} 的股东代码")
            return None

        result = self._api.send_order(
            client_id=self._client_id,
            category=category,
            price_type=price_type,
            gddm=gddm,
            zqdm=order.code,
            price=order.price,
            quantity=order.shares,
        )
        if result.get("success") == 1 or result.get("success") == True:
            # 解析成交回报
            return Trade(
                code=order.code,
                direction=order.direction,
                price=order.price,
                shares=order.shares,
                amount=order.price * order.shares,
                commission=0.0,  # 实盘由券商端计算
                stamp_tax=0.0,
                date=order.date,
                reason=order.reason,
            )
        else:
            logger.error(f"TDX 下单失败: {result}")
            return None

    def _cancel_order(self, order_id: str) -> bool:
        # order_id 格式: "hth"（合同号），在撤单时需要 exchange_id
        parts = order_id.split("_")
        if len(parts) < 2:
            return False
        exchange_id = int(parts[0])
        hth = parts[1] if len(parts) > 1 else order_id
        result = self._api.cancel_order(self._client_id, exchange_id, hth)
        return result.get("success") == 1 or result.get("success") == True

    def _query_positions(self) -> Dict[str, dict]:
        if not self._api or self._client_id < 0:
            return {}
        result = self._api.query_data(self._client_id, category=1)  # category 1=持仓
        if result.get("success") == 1:
            return self._parse_positions(result.get("data", []))
        return {}

    def _query_balance(self) -> dict:
        if not self._api or self._client_id < 0:
            return {}
        result = self._api.query_data(self._client_id, category=0)  # category 0=资金
        if result.get("success") == 1:
            data = result.get("data", {})
            return {
                "cash": data.get("可用金额", data.get("可用", 0)),
                "total_value": data.get("总资产", data.get("市值", 0)) + data.get("可用金额", data.get("可用", 0)),
                "market_value": data.get("市值", 0),
            }
        return {}

    def _get_market(self, code: str) -> int:
        """判断市场：0=深圳, 1=上海"""
        code = code.strip().zfill(6)
        if code.startswith(("6", "5", "9")):
            return 1  # 上海
        return 0  # 深圳

    def _get_gddm(self, market: int) -> Optional[str]:
        """获取股东代码"""
        result = self._api.query_data(self._client_id, category=5)  # category 5=股东代码
        if result.get("success") != 1:
            return None
        data_list = result.get("data", [])
        if not isinstance(data_list, list):
            return None
        for item in data_list:
            if isinstance(item, dict) and item.get("market") == market:
                return item.get("gddm")
        # 兜底：取第一个
        if data_list and isinstance(data_list[0], dict):
            return data_list[0].get("gddm")
        return None

    def _parse_positions(self, data: list) -> Dict[str, dict]:
        positions = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            code = item.get("证券代码", "")
            if not code:
                continue
            positions[code] = {
                "code": code,
                "name": item.get("证券名称", code),
                "shares": item.get("股票数量", 0),
                "cost_price": item.get("成本价", 0),
                "buy_date": item.get("买入日期", ""),
                "market_value": item.get("市值", 0),
            }
        return positions

    @property
    def client_id(self) -> int:
        return self._client_id

    @property
    def holdings(self):
        return self._broker.holdings