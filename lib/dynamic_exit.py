"""动态退出规则 —— 基于市场状态自适应调整

1. 基于波动率的仓位管理：高波动自动减仓
2. 动态止盈策略：基于波动率的档位调整
3. 市场状态 → 出场天数映射（V6 设计）

P0-2 (2026-06-08): 删除 `DynamicLossBreaker`（死代码 — backtest 从未实例化）
   和 `LossBreakerConfig`。`DynamicExitManager` 改用 `ConsecutiveLossBreaker`
   （from `lib.partial_exit`）。Regime 信息改为存放在 `DynamicExitManager._current_regime`。
   决策：(b) 删除而非接入 — 单一实现 + 减设计债。

使用方式：
    from lib.dynamic_exit import VolatilityPositionSizer, DynamicTakeProfit, DynamicExitManager
"""

import logging
import numpy as np
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ========== 波动率仓位管理 ==========

@dataclass
class VolPositionConfig:
    """波动率仓位配置"""
    base_position_pct: float = 0.10  # 基础仓位 10%
    max_position_pct: float = 0.20  # 最大仓位 20%
    min_position_pct: float = 0.03  # 最小仓位 3%

    # 波动率阈值（年化波动率）
    low_vol_threshold: float = 0.20   # <20% = 低波动
    high_vol_threshold: float = 0.40  # >40% = 高波动

    # 仓位调整系数
    low_vol_scalar: float = 1.3    # 低波动时放大
    high_vol_scalar: float = 0.5   # 高波动时缩小


class VolatilityPositionSizer:
    """基于波动率的动态仓位管理"""

    def __init__(self, config: VolPositionConfig = None):
        self.config = config or VolPositionConfig()

    def get_position_pct(self, annualized_vol: float) -> float:
        """根据波动率计算仓位比例

        Args:
            annualized_vol: 年化波动率（如 0.30 = 30%）

        Returns:
            float: 仓位比例 [min, max]
        """
        if annualized_vol <= 0:
            return self.config.base_position_pct

        if annualized_vol < self.config.low_vol_threshold:
            scalar = self.config.low_vol_scalar
        elif annualized_vol > self.config.high_vol_threshold:
            scalar = self.config.high_vol_scalar
        else:
            # 线性插值
            ratio = (annualized_vol - self.config.low_vol_threshold) / (
                self.config.high_vol_threshold - self.config.low_vol_threshold
            )
            scalar = self.config.low_vol_scalar + ratio * (
                self.config.high_vol_scalar - self.config.low_vol_scalar
            )

        position = self.config.base_position_pct * scalar
        return max(self.config.min_position_pct, min(self.config.max_position_pct, position))


# ========== 动态止盈 ==========

@dataclass
class DynamicTPConfig:
    """动态止盈配置"""
    # 基础档位
    base_levels = [
        (0.60, 0.33),  # +60% 减 1/3
        (0.30, 0.33),  # +30% 减 1/3
        (0.00, 1.00),  # 回到成本 全出
    ]

    # 高波动时降低止盈目标（更早锁定利润）
    high_vol_reduction: float = 0.15  # 档位降低 15%

    # 低波动时提高止盈目标（让利润奔跑）
    low_vol_boost: float = 0.10  # 档位提高 10%


class DynamicTakeProfit:
    """基于波动率的动态止盈"""

    def __init__(self, config: DynamicTPConfig = None):
        self.config = config or DynamicTPConfig()

    def get_exit_levels(self, annualized_vol: float) -> list:
        """根据波动率返回调整后的止盈档位

        Args:
            annualized_vol: 年化波动率

        Returns:
            list of (trigger_pct, exit_ratio) tuples
        """
        if annualized_vol > 0.40:
            # 高波动：降低止盈目标
            factor = 1.0 - self.config.high_vol_reduction
        elif annualized_vol < 0.20:
            # 低波动：提高止盈目标
            factor = 1.0 + self.config.low_vol_boost
        else:
            factor = 1.0

        levels = []
        for trigger, ratio in self.config.base_levels:
            adjusted_trigger = max(0.05, trigger * factor)
            levels.append((adjusted_trigger, ratio))

        return levels


# ========== 综合动态退出管理器 ==========

class DynamicExitManager:
    """综合动态退出管理器

    集成 ConsecutiveLossBreaker（来自 lib.partial_exit）+ 波动率仓位 + 动态止盈
    + 市场状态 → 出场天数映射。
    """

    def __init__(self, exit_days_map: dict = None):
        # P0-2: 复用 ConsecutiveLossBreaker（统一实现），不再用 DynamicLossBreaker
        from lib.partial_exit import ConsecutiveLossBreaker
        self.loss_breaker = ConsecutiveLossBreaker(
            reduce_threshold=3, stop_threshold=5, cooldown_days=20
        )
        self.position_sizer = VolatilityPositionSizer()
        self.take_profit = DynamicTakeProfit()
        self._exit_days_map = exit_days_map
        self._current_regime = "MEAN_REVERSION"  # 默认

    def update_market_state(self, regime: str, annualized_vol: float):
        """更新市场状态"""
        self._current_regime = regime
        self._current_vol = annualized_vol

    def get_position_pct(self) -> float:
        """获取建议仓位比例"""
        lb_mult = self.loss_breaker.get_position_multiplier()
        vol_pct = self.position_sizer.get_position_pct(getattr(self, '_current_vol', 0.3))
        return vol_pct * lb_mult

    def get_exit_levels(self) -> list:
        """获取当前止盈档位"""
        return self.take_profit.get_exit_levels(getattr(self, '_current_vol', 0.3))

    def record_trade(self, code: str, pnl: float, date: str):
        """记录交易结果

        Args:
            code: 股票代码
            pnl: 单笔盈亏（正=赢，负=亏）
            date: 交易日
        """
        # P0-2: ConsecutiveLossBreaker 接口 (code, pnl, date) — 兼容
        self.loss_breaker.record_trade(code, pnl, date)

    def advance_day(self):
        """推进一天"""
        self.loss_breaker.advance_day()

    def get_status(self) -> Dict:
        """获取状态"""
        return {
            "loss_breaker": self.loss_breaker.get_status(),
            "position_pct": self.get_position_pct(),
            "exit_levels": self.get_exit_levels(),
            "current_vol": getattr(self, '_current_vol', 0),
            "current_regime": self._current_regime,
            "exit_days": self.get_exit_days(),
        }

    def get_exit_days(self) -> int:
        """根据市场状态获取建议出场天数

        V6 设计要求：
        - TREND_UP: 20天（让利润奔跑）
        - MEAN_REVERSION: 10天（快进快出）
        - HIGH_VOLATILITY: 5天（缩短周期）
        - LOW_VOLATILITY: 15天（耐心等待）
        - TREND_DOWN: 0天（不开仓）
        - RISK_OFF: 0天（禁止一切开仓）
        """
        regime = self._current_regime
        exit_days_map = self._exit_days_map if self._exit_days_map is not None else {
            "TREND_UP": 20,
            "MEAN_REVERSION": 10,
            "HIGH_VOLATILITY": 5,
            "LOW_VOLATILITY": 15,
            "TREND_DOWN": 0,
            "RISK_OFF": 0,
        }
        return exit_days_map.get(regime, 10)  # 默认 10 天

