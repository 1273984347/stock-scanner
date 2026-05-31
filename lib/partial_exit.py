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
from config import get_config

logger = logging.getLogger(__name__)


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

DEFAULT_EXIT_LEVELS = _build_exit_levels()


class PartialExitManager:
    """分仓变速止盈管理器"""

    def __init__(self, exit_levels: List[ExitLevel] = None):
        self.exit_levels = exit_levels or DEFAULT_EXIT_LEVELS
        self._positions: Dict[str, dict] = {}  # code -> position info

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
        exit_shares = int(remaining * level.exit_ratio / 100) * 100  # 整手
        if exit_shares <= 0:
            exit_shares = remaining  # 全部退出

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
    - 连续亏损 2 笔 → 仓位减半
    - 连续亏损 3 笔 → 停止交易
    - 盈利 1 笔 → 重置计数器
    """

    def __init__(self, reduce_threshold: int = 3, stop_threshold: int = 5,
                 cooldown_days: int = 20):
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
        """是否已停止交易（含冷却期自动恢复）"""
        if not self._is_stopped:
            return False
        if self.cooldown_days > 0 and self._current_day - self._stop_day >= self.cooldown_days:
            self.reset()
            logger.info(f"【冷却期结束】{self.cooldown_days}天已过，自动恢复交易")
            return False
        return True

    def advance_day(self):
        """推进一天（用于冷却期计数）"""
        self._current_day += 1

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
            "is_forced": total < 0.5,  # 标记为"勉强单"
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
            stop_dist = data.get("stop_distance_pct", 0.05)
            if stop_dist <= 0.03:
                return 1.0
            elif stop_dist <= 0.05:
                return 0.7
            elif stop_dist <= 0.08:
                return 0.4
            return 0.1

        elif name == "盈亏比":
            ratio = data.get("expected_pnl_ratio", 1.0)
            if ratio >= 3.0:
                return 1.0
            elif ratio >= 2.0:
                return 0.7
            elif ratio >= 1.5:
                return 0.4
            return 0.1

        elif name == "信号一致性":
            consensus = data.get("signal_consensus", "mixed")
            if consensus == "bullish":
                return 1.0
            elif consensus == "mixed":
                return 0.5
            return 0.0

        return 0.5  # 默认中性
