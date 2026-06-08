"""分仓变速止盈 + 连续亏损熔断 + 开仓质量自评分

V6 设计要求：
1. 分仓变速止盈：60-70% 减 1/3 → 中线减 1/3 → 成本价持剩余
2. 硬性中断：最大连续亏损 3 次停交易
3. 开仓质量自评分：每项 0-1 分，复盘识别"勉强单"

使用方式：
    from lib.partial_exit import PartialExitManager, QualityScorer
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from config import get_config, RiskConfig

logger = logging.getLogger(__name__)


# === Agent 1 Constants === (P2 #5: 与 dynamic_exit 共享同一份阈值；P2 #9 + #11: 提常量便于调参)
# 从 lib.dynamic_exit 复用 — 单一权威实现（lib.partial_exit.ConsecutiveLossBreaker 是真正使用者）
from lib.dynamic_exit import (
    DEFAULT_CONSEC_LOSS_REDUCE,
    DEFAULT_CONSEC_LOSS_STOP,
    DEFAULT_CONSEC_LOSS_COOLDOWN_DAYS,
)

# P2 #11: "勉强单" 阈值 — 与 verdict 分档 (0.4/0.6/0.8) 解耦时使用统一常量
# §4.2 A3 迁移 (2026-06-08): 原模块级常量 → config.RiskConfig.forced_threshold
FORCED_THRESHOLD = RiskConfig().forced_threshold

# P2 #9: 评分维度的 tier 阈值（止损距离 / 盈亏比）— dataclass 顶部集中
# 注：这些是评分分档阈值，调节 score() 输出，不属于业务参数
STOP_DIST_TIERS: List[Tuple[float, float]] = [
    (0.03, 1.0),   # 止损 ≤3% → 1.0 (风险极小)
    (0.05, 0.7),   # 止损 ≤5% → 0.7 (风险可控)
    (0.08, 0.4),   # 止损 ≤8% → 0.4 (风险偏高)
    (float("inf"), 0.1),  # 止损 >8% → 0.1 (风险高)
]
PNL_RATIO_TIERS: List[Tuple[float, float]] = [
    (3.0, 1.0),    # 盈亏比 ≥3:1 → 1.0
    (2.0, 0.7),    # ≥2:1 → 0.7
    (1.5, 0.4),    # ≥1.5:1 → 0.4
    (float("inf"), 0.1),  # <1.5:1 → 0.1
]


# ========== 分仓变速止盈 ==========

@dataclass
class ExitLevel:
    """止盈级别"""
    trigger_pct: float      # 触发收益率（如 0.60 = +60%）
    exit_ratio: float       # 退出比例（如 0.33 = 减 1/3）
    description: str        # 描述


def _build_exit_levels():
    """从 config 构建退出级别"""
    cfg = get_config("balanced")
    return [
        ExitLevel(
            trigger_pct=lv["profit_threshold"],
            exit_ratio=lv["sell_ratio"],
            description=lv["description"],
        )
        for lv in cfg.risk.partial_exit_levels
    ]


# P2 #12: 不在 import 时执行 get_config() — 改用 factory 在 __init__ 中惰性求值
# 原因：
#   1) 模块导入副作用污染测试（pytest 收集阶段触发 config 初始化 + file I/O）
#   2) preset 切换需要重建 levels（每次都用 balanced 不灵活）
#   3) 减少冷启动时延（按需加载）
def _default_exit_levels_factory() -> List[ExitLevel]:
    """DEFAULT_EXIT_LEVELS 的惰性求值工厂（PartialExitManager.__init__ 内部用）"""
    return _build_exit_levels()


class PartialExitManager:
    """分仓变速止盈管理器

    V6 设计：止盈档位随市场状态自适应调整。
    - TREND_UP: 提高止盈目标（+30%），让利润奔跑
    - MEAN_REVERSION: 降低止盈目标（-30%），快进快出
    - HIGH_VOLATILITY/RISK_OFF: 更早止盈（-50%），锁定利润
    """

    # 市场状态 → 止盈目标调整系数
    REGIME_ADJUSTMENT = {
        "TREND_UP": 1.3,
        "LOW_VOLATILITY": 1.1,
        "MEAN_REVERSION": 0.7,
        "HIGH_VOLATILITY": 0.5,
        "TREND_DOWN": 0.5,
        "RISK_OFF": 0.3,
    }

    def __init__(self, exit_levels: List[ExitLevel] = None, regime: str = None):
        # P2 #12: 惰性求值 — 仅在第一次真正需要时调用 get_config
        self._base_levels = exit_levels if exit_levels is not None else _default_exit_levels_factory()
        self._regime = regime or "MEAN_REVERSION"
        self.exit_levels = self._adjust_levels()
        self._positions: Dict[str, dict] = {}  # code -> position info

    def update_regime(self, regime: str):
        """更新市场状态，重新计算止盈档位"""
        if regime != self._regime:
            self._regime = regime
            self.exit_levels = self._adjust_levels()

    def _adjust_levels(self) -> List[ExitLevel]:
        """根据市场状态调整止盈档位"""
        adj = self.REGIME_ADJUSTMENT.get(self._regime, 1.0)
        return [
            ExitLevel(
                trigger_pct=lv.trigger_pct * adj,
                exit_ratio=lv.exit_ratio,
                description=f"{lv.description} [regime={self._regime}, adj={adj:.1f}]",
            )
            for lv in self._base_levels
        ]

    def register_entry(self, code: str, entry_price: float, total_shares: int,
                       entry_date: str):
        """注册入场"""
        self._positions[code] = {
            "entry_price": entry_price,
            "total_shares": total_shares,
            "remaining_shares": total_shares,
            "entry_date": entry_date,
            "current_level": 0,  # 当前止盈档位
            "peak_price": entry_price,  # 持仓期间最高价
            "exits": [],  # 已执行的退出记录
        }

    def check_exit(self, code: str, current_price: float, current_date: str) -> Optional[Dict]:
        """检查是否需要执行止盈

        Returns:
            None 或 {"action": "SELL", "shares": N, "reason": "...", "level": i}
        """
        pos = self._positions.get(code)
        if pos is None:
            return None

        remaining = pos["remaining_shares"]
        if remaining <= 0:
            return None

        entry = pos["entry_price"]
        pnl_pct = (current_price - entry) / entry

        # 更新最高价
        pos["peak_price"] = max(pos["peak_price"], current_price)
        peak_pnl = (pos["peak_price"] - entry) / entry

        current_level = pos["current_level"]

        # 检查是否触发下一档止盈
        for i in range(current_level, len(self.exit_levels)):
            level = self.exit_levels[i]

            # 第一档：收益率直接达到触发点
            if i == 0 and pnl_pct >= level.trigger_pct:
                return self._execute_exit(code, pos, i, current_price, current_date, level)

            # 后续档位：从最高点回落到触发点
            if i > 0 and peak_pnl >= self.exit_levels[i-1].trigger_pct:
                if pnl_pct <= level.trigger_pct:
                    return self._execute_exit(code, pos, i, current_price, current_date, level)

        return None

    def _execute_exit(self, code: str, pos: dict, level_idx: int,
                      price: float, date: str, level: ExitLevel) -> Dict:
        """执行退出"""
        remaining = pos["remaining_shares"]
        # P2 #3: 整手计算 bug 修复
        # 原公式：int(remaining * level.exit_ratio / 100) * 100
        # 问题：level.exit_ratio 是小数（如 0.33 / 0.5 / 1.0），除以 100 变成 0.0033/0.005/0.01
        #       → 950 股 * 1.0 / 100 = 9.5 → int(9.5) * 100 = 900 → 剩 50 股**永远不退出**
        #       因为 line 148 兜底是 exit_shares <= 0（这里 900 > 0 不触发）
        # 修复策略 1（保留整手语义）：level.exit_ratio == 1.0 → 全清；否则整手向下取整
        if level.exit_ratio >= 1.0:
            exit_shares = remaining
        else:
            exit_shares = int(remaining * level.exit_ratio) // 100 * 100
            if exit_shares <= 0:
                # 整手取整后为 0：可能剩余不足 1 手（< 100 股），全清兜底
                exit_shares = remaining

        exit_shares = min(exit_shares, remaining)
        pos["remaining_shares"] -= exit_shares
        pos["current_level"] = level_idx + 1

        pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]
        pos["exits"].append({
            "date": date,
            "price": price,
            "shares": exit_shares,
            "pnl_pct": pnl_pct,
            "level": level_idx,
        })

        reason = f"{level.description} (收益 {pnl_pct:.1%})"
        logger.info(f"止盈 {code}: {reason}, 卖出 {exit_shares} 股, 剩余 {pos['remaining_shares']}")

        return {
            "action": "SELL",
            "code": code,
            "shares": exit_shares,
            "reason": reason,
            "level": level_idx,
            "remaining": pos["remaining_shares"],
        }

    def get_position_status(self, code: str) -> Optional[Dict]:
        """获取持仓状态"""
        pos = self._positions.get(code)
        if pos is None:
            return None
        return {
            "entry_price": pos["entry_price"],
            "remaining": pos["remaining_shares"],
            "total": pos["total_shares"],
            "current_level": pos["current_level"],
            "peak_price": pos["peak_price"],
            "exits_count": len(pos["exits"]),
        }


# ========== 连续亏损熔断 ==========

class ConsecutiveLossBreaker:
    """连续亏损熔断器

    规则：
    - 连续亏损 reduce_threshold 笔 → 仓位减半（默认 3 笔）
    - 连续亏损 stop_threshold 笔 → 停止交易（默认 5 笔）
    - 盈利 1 笔 → 重置计数器
    """

    # P2 #5: 默认值与 lib.dynamic_exit 中常量保持一致（单一权威）
    def __init__(self, reduce_threshold: int = DEFAULT_CONSEC_LOSS_REDUCE,
                 stop_threshold: int = DEFAULT_CONSEC_LOSS_STOP,
                 cooldown_days: int = DEFAULT_CONSEC_LOSS_COOLDOWN_DAYS):
        self.reduce_threshold = reduce_threshold
        self.stop_threshold = stop_threshold
        self.cooldown_days = cooldown_days
        self._consecutive_losses: int = 0
        self._is_stopped: bool = False
        self._stop_day: int = 0
        self._current_day: int = 0
        self._trade_history: List[Dict] = []

    def record_trade(self, code: str, pnl: float, date: str):
        """记录交易结果"""
        is_win = pnl > 0
        self._trade_history.append({
            "code": code, "pnl": pnl, "date": date, "is_win": is_win
        })

        if is_win:
            if self._consecutive_losses > 0:
                logger.info(f"盈利交易 {code} ({pnl:.2%})，重置连亏计数器")
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            logger.warning(f"亏损交易 {code} ({pnl:.2%})，连续亏损 {self._consecutive_losses} 笔")

            if self._consecutive_losses >= self.stop_threshold:
                self._is_stopped = True
                self._stop_day = self._current_day
                logger.warning(f"【熔断】连续亏损 {self._consecutive_losses} 笔，停止交易（{self.cooldown_days}天后自动恢复）")

    def should_reduce(self) -> bool:
        """是否应减仓"""
        return self._consecutive_losses >= self.reduce_threshold

    def is_stopped(self) -> bool:
        """是否已停止交易（纯查询 — 不修改任何状态）

        P0-3 修复：原实现 line 241 在查询中调用 self.reset() 副作用，破坏查询
        语义（同一实例多次调用结果不一致）。冷却期自动重置逻辑已搬到
        `_check_auto_reset()`，由 `advance_day()` 显式调用。

        返回值语义：
        - 未触发熔断 → False
        - 触发熔断 + 冷却期未满 → True
        - 触发熔断 + 冷却期已满（_current_day - _stop_day >= cooldown_days）→ False
          （caller 需调 advance_day() 才会真正重置 _is_stopped 字段）
        """
        if not self._is_stopped:
            return False
        if self.cooldown_days > 0 and self._current_day - self._stop_day >= self.cooldown_days:
            return False  # 冷却期已过（仅查询；advance_day 负责重置字段）
        return True

    def _check_auto_reset(self):
        """检查并执行冷却期自动重置（由 advance_day 调用）

        P0-3 新增：从 is_stopped() 抽出的副作用逻辑，caller 通过 advance_day()
        显式推进时间时触发，确保查询无副作用。
        """
        if (self._is_stopped
                and self.cooldown_days > 0
                and self._current_day - self._stop_day >= self.cooldown_days):
            self.reset()
            logger.info(f"【冷却期结束】{self.cooldown_days}天已过，自动恢复交易")

    def advance_day(self):
        """推进一天（用于冷却期计数 + 触发冷却期自动重置）"""
        self._current_day += 1
        self._check_auto_reset()

    def get_position_multiplier(self) -> float:
        """获取仓位乘数"""
        if self._is_stopped:
            return 0.0
        if self._consecutive_losses >= self.reduce_threshold:
            return 0.5
        return 1.0

    def reset(self):
        """重置（手动恢复交易）"""
        self._consecutive_losses = 0
        self._is_stopped = False
        logger.info("连亏熔断器已重置")

    def get_status(self) -> Dict:
        """获取状态"""
        return {
            "consecutive_losses": self._consecutive_losses,
            "is_stopped": self._is_stopped,
            "position_multiplier": self.get_position_multiplier(),
            "total_trades": len(self._trade_history),
            "recent_trades": self._trade_history[-5:],
        }


# ========== 开仓质量自评分 ==========

@dataclass
class QualityDimension:
    """质量维度"""
    name: str
    weight: float
    description: str


QUALITY_DIMENSIONS = [
    QualityDimension("趋势确认", 0.20, "价格 > EMA21 > EMA55，趋势方向明确"),
    QualityDimension("量能配合", 0.15, "成交量 > 20日均量，放量突破"),
    QualityDimension("板块强度", 0.15, "所属板块涨幅前 30%"),
    QualityDimension("资金流入", 0.10, "主力净流入 > 0"),
    QualityDimension("市场状态", 0.10, "当前市场状态为 TREND_UP 或 MEAN_REVERSION"),
    QualityDimension("止损空间", 0.10, "止损距离 < 5%（风险可控）"),
    QualityDimension("盈亏比", 0.10, "预期盈亏比 > 2:1"),
    QualityDimension("信号一致性", 0.10, "多因子信号方向一致"),
]


class QualityScorer:
    """开仓质量自评分器"""

    def __init__(self, dimensions: List[QualityDimension] = None):
        self.dimensions = dimensions or QUALITY_DIMENSIONS

    def score(self, entry_data: Dict) -> Dict:
        """对一次开仓进行质量评分

        Args:
            entry_data: 开仓数据，包含：
                - trend_confirmed: bool, 趋势确认
                - volume_confirmed: bool, 量能配合
                - sector_rank_pct: float, 板块排名百分位 (0-1, 越小越好)
                - net_inflow: float, 主力净流入 (正=流入)
                - market_regime: str, 市场状态
                - stop_distance_pct: float, 止损距离百分比
                - expected_pnl_ratio: float, 预期盈亏比
                - signal_consensus: str, 信号一致性 ("bullish"/"bearish"/"mixed")

        Returns:
            {"total_score": float, "dimensions": {name: score}, "verdict": str}
        """
        scores = {}
        total = 0.0

        for dim in self.dimensions:
            score = self._score_dimension(dim, entry_data)
            scores[dim.name] = round(score, 2)
            total += score * dim.weight

        total = round(total, 2)

        if total >= 0.8:
            verdict = "优质开仓"
        elif total >= 0.6:
            verdict = "合格开仓"
        elif total >= 0.4:
            verdict = "勉强开仓"
        else:
            verdict = "不建议开仓"

        return {
            "total_score": total,
            "dimensions": scores,
            "verdict": verdict,
            # P2 #11: 提常量 — 避免与 verdict 分档 (0.4/0.6/0.8) 隐式耦合
            "is_forced": total < FORCED_THRESHOLD,
        }

    def _score_dimension(self, dim: QualityDimension, data: Dict) -> float:
        """单维度评分 (0-1)"""
        name = dim.name

        if name == "趋势确认":
            return 1.0 if data.get("trend_confirmed", False) else 0.0

        elif name == "量能配合":
            return 1.0 if data.get("volume_confirmed", False) else 0.0

        elif name == "板块强度":
            rank_pct = data.get("sector_rank_pct", 0.5)
            # 排名前 30% = 1.0, 后 30% = 0.0
            return max(0, 1.0 - rank_pct * 2)

        elif name == "资金流入":
            inflow = data.get("net_inflow", 0)
            return 1.0 if inflow > 0 else 0.0

        elif name == "市场状态":
            regime = data.get("market_regime", "")
            if regime in ("TREND_UP",):
                return 1.0
            elif regime in ("MEAN_REVERSION", "LOW_VOLATILITY"):
                return 0.7
            elif regime in ("HIGH_VOLATILITY",):
                return 0.3
            return 0.0  # TREND_DOWN, RISK_OFF

        elif name == "止损空间":
            # P2 #9: 提常量到模块顶部 STOP_DIST_TIERS
            stop_dist = data.get("stop_distance_pct", 0.05)
            for threshold, score in STOP_DIST_TIERS:
                if stop_dist <= threshold:
                    return score
            return 0.1  # fallback（实际不会被触发，因 STOP_DIST_TIERS 含 inf）

        elif name == "盈亏比":
            # P2 #9: 提常量到模块顶部 PNL_RATIO_TIERS
            ratio = data.get("expected_pnl_ratio", 1.0)
            for threshold, score in PNL_RATIO_TIERS:
                if ratio >= threshold:
                    return score
            return 0.1  # fallback

        elif name == "信号一致性":
            consensus = data.get("signal_consensus", "mixed")
            if consensus == "bullish":
                return 1.0
            elif consensus == "mixed":
                return 0.5
            return 0.0

        return 0.5  # 默认中性
