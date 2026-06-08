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

from config import RiskConfig

logger = logging.getLogger(__name__)


# === Agent 1 Constants === (P2 #4 + #5: 提常量便于 preset/调参)
# §4.2 A3 迁移 (2026-06-08): 原模块级常量 → config.RiskConfig 统一管理
# V6 设计的 regime → 出场天数映射（balanced preset；aggressive 可覆盖为更短天数）
# 5 个 string regime + 默认 fallback（regime 不在表内时返回 10 天）
_risk_config = RiskConfig()
DEFAULT_EXIT_DAYS_MAP: dict = _risk_config.exit_days_map
DEFAULT_EXIT_DAYS_FALLBACK = 10

# ConsecutiveLossBreaker 阈值（与 lib.partial_exit 中保持一致 — 单一权威）
DEFAULT_CONSEC_LOSS_REDUCE = 3
DEFAULT_CONSEC_LOSS_STOP = _risk_config.consec_loss_stop
DEFAULT_CONSEC_LOSS_COOLDOWN_DAYS = _risk_config.consec_loss_cooldown_days


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

    # P2 #1: 与 VolPositionConfig 共享同一阈值语义（避免调一处漂移）
    # 注：Dataclass 字段若想复用 VolPositionConfig 的同名字段，可改 type 注解
    # 为 `VolPositionConfig` 并设 default_factory；此处维持 float 简单些
    high_vol_threshold: float = 0.40  # >40% 年化波动 = 高波动（降档位）
    low_vol_threshold: float = 0.20   # <20% 年化波动 = 低波动（升档位）

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
        # P2 #1: 改用 self.config 字段（之前硬编码 0.40/0.20 与 VolPositionConfig 漂移风险）
        if annualized_vol > self.config.high_vol_threshold:
            # 高波动：降低止盈目标
            factor = 1.0 - self.config.high_vol_reduction
        elif annualized_vol < self.config.low_vol_threshold:
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

    def __init__(self, exit_days_map: dict = None,
                 reduce_threshold: int = DEFAULT_CONSEC_LOSS_REDUCE,
                 stop_threshold: int = DEFAULT_CONSEC_LOSS_STOP,
                 cooldown_days: int = DEFAULT_CONSEC_LOSS_COOLDOWN_DAYS):
        # P0-2: 复用 ConsecutiveLossBreaker（统一实现），不再用 DynamicLossBreaker
        # P2 #5: 阈值从模块常量传入，与 lib.partial_exit.DEFAULT_CONSEC_* 对齐
        from lib.partial_exit import ConsecutiveLossBreaker
        self.loss_breaker = ConsecutiveLossBreaker(
            reduce_threshold=reduce_threshold,
            stop_threshold=stop_threshold,
            cooldown_days=cooldown_days,
        )
        self.position_sizer = VolatilityPositionSizer()
        self.take_profit = DynamicTakeProfit()
        # P2 #4: 默认用模块常量；调用方可传 preset-specific 覆盖
        self._exit_days_map = exit_days_map if exit_days_map is not None else DEFAULT_EXIT_DAYS_MAP
        self._current_regime = "MEAN_REVERSION"  # 默认
        # P2 #10: 显式初始化避免 getattr 防御式访问 + 不一致（line 184 用 0 fallback）
        self._current_vol: float = 0.3

    def update_market_state(self, regime: str, annualized_vol: float):
        """更新市场状态"""
        self._current_regime = regime
        self._current_vol = annualized_vol

    def get_position_pct(self) -> float:
        """获取建议仓位比例"""
        lb_mult = self.loss_breaker.get_position_multiplier()
        vol_pct = self.position_sizer.get_position_pct(self._current_vol)
        return vol_pct * lb_mult

    def get_exit_levels(self) -> list:
        """获取当前止盈档位"""
        return self.take_profit.get_exit_levels(self._current_vol)

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
            "current_vol": self._current_vol,
            "current_regime": self._current_regime,
            "exit_days": self.get_exit_days(),
        }

    def get_exit_days(self) -> int:
        """根据市场状态获取建议出场天数

        V6 设计要求（详见模块顶层 DEFAULT_EXIT_DAYS_MAP）：
        - TREND_UP: 20天（让利润奔跑）
        - MEAN_REVERSION: 10天（快进快出）
        - HIGH_VOLATILITY: 5天（缩短周期）
        - LOW_VOLATILITY: 15天（耐心等待）
        - TREND_DOWN: 0天（不开仓）
        - RISK_OFF: 0天（禁止一切开仓）
        """
        # P2 #4: 不再内联 dict 默认值 — 改用模块顶层 DEFAULT_EXIT_DAYS_MAP
        return self._exit_days_map.get(self._current_regime, DEFAULT_EXIT_DAYS_FALLBACK)

