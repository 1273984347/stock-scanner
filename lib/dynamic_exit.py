"""动态退出规则 —— 基于市场状态自适应调整

1. 动态 loss breaker 阈值：牛市放宽、熊市收紧
2. 基于波动率的仓位管理：高波动自动减仓
3. 动态止盈策略：基于波动率的档位调整

使用方式：
    from lib.dynamic_exit import DynamicLossBreaker, VolatilityPositionSizer, DynamicTakeProfit
"""

import logging
import numpy as np
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ========== 动态 Loss Breaker ==========

@dataclass
class LossBreakerConfig:
    """Loss Breaker 配置"""
    # 默认阈值
    base_reduce_threshold: int = 3
    base_stop_threshold: int = 5
    cooldown_days: int = 20

    # 市场状态调整系数
    # 牛市（TREND_UP）：放宽阈值
    trend_up_reduce_mult: float = 1.5
    trend_up_stop_mult: float = 1.5

    # 熊市（TREND_DOWN）：收紧阈值
    trend_down_reduce_mult: float = 0.7
    trend_down_stop_mult: float = 0.7

    # 震荡（COMPRESSION/MEAN_REVERSION）：不变
    neutral_mult: float = 1.0


class DynamicLossBreaker:
    """基于市场状态的动态 Loss Breaker"""

    def __init__(self, config: LossBreakerConfig = None):
        self.config = config or LossBreakerConfig()
        self.consecutive_losses = 0
        self.cooldown_remaining = 0
        self.is_stopped = False
        self.current_regime = "UNKNOWN"
        self._effective_reduce = self.config.base_reduce_threshold
        self._effective_stop = self.config.base_stop_threshold

    def update_regime(self, regime: str):
        """更新市场状态，动态调整阈值"""
        self.current_regime = regime

        if regime == "TREND_UP":
            mult = self.config.trend_up_reduce_mult
            stop_mult = self.config.trend_up_stop_mult
        elif regime in ("TREND_DOWN", "RISK_OFF"):
            mult = self.config.trend_down_reduce_mult
            stop_mult = self.config.trend_down_stop_mult
        else:
            mult = self.config.neutral_mult
            stop_mult = self.config.neutral_mult

        self._effective_reduce = max(2, int(self.config.base_reduce_threshold * mult))
        self._effective_stop = max(3, int(self.config.base_stop_threshold * stop_mult))

        logger.debug(
            f"LossBreaker regime={regime}: reduce={self._effective_reduce} stop={self._effective_stop}"
        )

    def record_trade(self, is_win: bool):
        """记录交易结果"""
        if is_win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        # 检查是否触发熔断
        if self.consecutive_losses >= self._effective_stop:
            self.is_stopped = True
            self.cooldown_remaining = self.config.cooldown_days
            logger.warning(
                f"LossBreaker 触发! 连亏{self.consecutive_losses}笔，"
                f"阈值{self._effective_stop}(regime={self.current_regime})，"
                f"冷却{self.cooldown_remaining}天"
            )

    def advance_day(self):
        """推进一天"""
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            if self.cooldown_remaining <= 0:
                self.is_stopped = False
                self.consecutive_losses = 0
                logger.info("LossBreaker 冷却期结束，恢复交易")

    def get_position_multiplier(self) -> float:
        """获取仓位乘数"""
        if self.is_stopped:
            return 0.0

        if self.consecutive_losses >= self._effective_reduce:
            return 0.5

        return 1.0

    def get_status(self) -> Dict:
        return {
            "is_stopped": self.is_stopped,
            "consecutive_losses": self.consecutive_losses,
            "cooldown_remaining": self.cooldown_remaining,
            "regime": self.current_regime,
            "effective_reduce": self._effective_reduce,
            "effective_stop": self._effective_stop,
            "position_multiplier": self.get_position_multiplier(),
        }


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

    集成动态 Loss Breaker + 波动率仓位 + 动态止盈
    """

    def __init__(self):
        self.loss_breaker = DynamicLossBreaker()
        self.position_sizer = VolatilityPositionSizer()
        self.take_profit = DynamicTakeProfit()

    def update_market_state(self, regime: str, annualized_vol: float):
        """更新市场状态"""
        self.loss_breaker.update_regime(regime)
        self._current_vol = annualized_vol

    def get_position_pct(self) -> float:
        """获取建议仓位比例"""
        lb_mult = self.loss_breaker.get_position_multiplier()
        vol_pct = self.position_sizer.get_position_pct(getattr(self, '_current_vol', 0.3))
        return vol_pct * lb_mult

    def get_exit_levels(self) -> list:
        """获取当前止盈档位"""
        return self.take_profit.get_exit_levels(getattr(self, '_current_vol', 0.3))

    def record_trade(self, is_win: bool):
        """记录交易结果"""
        self.loss_breaker.record_trade(is_win)

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
            "exit_days": self.get_exit_days(),
        }

    def get_exit_days(self) -> int:
        """根据市场状态获取建议出场天数

        基于回测数据：
        - TREND_UP: 10天（让利润奔跑）
        - MEAN_REVERSION: 5天（快进快出）
        - HIGH_VOLATILITY: 5天（降低风险）
        - LOW_VOLATILITY: 15天（耐心等待）
        - TREND_DOWN: 3天（尽快退出）
        - RISK_OFF: 0天（不进场）
        """
        regime = self.loss_breaker.current_regime
        exit_days_map = {
            "TREND_UP": 10,
            "MEAN_REVERSION": 5,
            "HIGH_VOLATILITY": 5,
            "LOW_VOLATILITY": 15,
            "TREND_DOWN": 3,
            "RISK_OFF": 0,
        }
        return exit_days_map.get(regime, 10)  # 默认 10 天
