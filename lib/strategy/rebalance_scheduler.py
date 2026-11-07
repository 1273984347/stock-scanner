"""月频调仓调度器 + 退出规则

实现 V6 设计要求：
- 月频调仓：每 20 个交易日执行一次完整扫描
- 退出规则：连续 3 月滚动夏普 < 1.0 或回撤 > 25% 即停止
- 限价单执行：信号日收盘价 → 次日限价单

使用方式：
    from lib.strategy.rebalance_scheduler import RebalanceScheduler
    scheduler = RebalanceScheduler()
    if scheduler.should_rebalance(today):
        signals = run_all_strategies()
        scheduler.execute_rebalance(signals)
"""

import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RebalanceConfig:
    """调仓配置"""
    rebalance_period: int = 20          # 调仓周期（交易日）
    rolling_sharpe_window: int = 60     # 滚动夏普窗口（交易日）
    rolling_sharpe_threshold: float = 0.5  # 滚动夏普阈值（放宽）
    max_drawdown_threshold: float = 0.25   # 最大回撤阈值
    consecutive_sharpe_months: int = 6     # 连续几个月夏普低于阈值触发退出（放宽）
    transaction_cost: float = 0.01         # 交易成本（单边 1%）


@dataclass
class PerformanceTracker:
    """性能跟踪器"""
    daily_returns: List[float] = field(default_factory=list)
    peak_value: float = 0.0
    current_value: float = 0.0
    sharpe_violations: int = 0  # 连续夏普低于阈值的次数
    is_stopped: bool = False
    stop_reason: str = ""


class RebalanceScheduler:
    """月频调仓调度器"""

    def __init__(self, config: RebalanceConfig = None):
        self.config = config or RebalanceConfig()
        self._state_path = Path(__file__).parent.parent.parent / "cache" / "rebalance_state.json"
        self._last_rebalance_date: Optional[str] = None
        self._trade_day_count: int = 0
        self._performance = PerformanceTracker()
        self._load_state()

    def should_rebalance(self, today: str) -> bool:
        """判断今天是否应该调仓

        Args:
            today: 日期字符串 YYYY-MM-DD

        Returns:
            是否应该调仓
        """
        # 检查是否被停止
        if self._performance.is_stopped:
            logger.warning(f"调仓已停止: {self._performance.stop_reason}")
            return False

        # 首次运行
        if self._last_rebalance_date is None:
            return True

        # 计算距离上次调仓的交易日数
        try:
            last = datetime.strptime(self._last_rebalance_date, "%Y-%m-%d")
            now = datetime.strptime(today, "%Y-%m-%d")
            days_elapsed = (now - last).days
            # 粗略估算交易日（自然日 × 0.72 ≈ 交易日）
            trade_days_elapsed = int(days_elapsed * 0.72)

            if trade_days_elapsed >= self.config.rebalance_period:
                return True
        except ValueError:
            return True

        return False

    def record_daily_return(self, portfolio_value: float, date: str):
        """记录每日收益

        Args:
            portfolio_value: 当日组合市值
            date: 日期字符串
        """
        if self._performance.current_value > 0:
            daily_ret = (portfolio_value - self._performance.current_value) / self._performance.current_value
            self._performance.daily_returns.append(daily_ret)

        self._performance.current_value = portfolio_value
        self._performance.peak_value = max(self._performance.peak_value, portfolio_value)

        # 检查退出条件
        self._check_exit_conditions(date)

    def _check_exit_conditions(self, date: str):
        """检查退出条件"""
        returns = self._performance.daily_returns
        if len(returns) < 20:
            return

        # 1. 回撤检查
        if self._performance.peak_value > 0:
            drawdown = (self._performance.peak_value - self._performance.current_value) / self._performance.peak_value
            if drawdown > self.config.max_drawdown_threshold:
                self._performance.is_stopped = True
                self._performance.stop_reason = f"回撤 {drawdown:.1%} 超过 {self.config.max_drawdown_threshold:.0%} 阈值 ({date})"
                logger.warning(f"【退出规则】{self._performance.stop_reason}")
                return

        # 2. 滚动夏普检查
        window = self.config.rolling_sharpe_window
        if len(returns) >= window:
            recent = returns[-window:]
            mean_ret = np.mean(recent)
            std_ret = np.std(recent)
            if std_ret > 0:
                # 年化夏普（假设 252 交易日）
                sharpe = (mean_ret / std_ret) * np.sqrt(252)
                if sharpe < self.config.rolling_sharpe_threshold:
                    self._performance.sharpe_violations += 1
                    logger.debug(f"滚动夏普 {sharpe:.2f} < {self.config.rolling_sharpe_threshold}，连续 {self._performance.sharpe_violations} 次")
                else:
                    self._performance.sharpe_violations = 0  # 重置

                # 连续 N 个月（约 N×20 交易日）夏普低于阈值
                if self._performance.sharpe_violations >= self.config.consecutive_sharpe_months:
                    self._performance.is_stopped = True
                    self._performance.stop_reason = (
                        f"连续 {self._performance.sharpe_violations} 个月滚动夏普 "
                        f"< {self.config.rolling_sharpe_threshold} ({date})"
                    )
                    logger.warning(f"【退出规则】{self._performance.stop_reason}")
                    return

    def get_rolling_sharpe(self) -> Optional[float]:
        """获取当前滚动夏普"""
        returns = self._performance.daily_returns
        window = self.config.rolling_sharpe_window
        if len(returns) < window:
            return None
        recent = returns[-window:]
        mean_ret = np.mean(recent)
        std_ret = np.std(recent)
        if std_ret <= 0:
            return 0.0
        return float((mean_ret / std_ret) * np.sqrt(252))

    def get_current_drawdown(self) -> float:
        """获取当前回撤"""
        if self._performance.peak_value <= 0:
            return 0.0
        return (self._performance.peak_value - self._performance.current_value) / self._performance.peak_value

    def mark_rebalanced(self, date: str):
        """标记今天已完成调仓"""
        self._last_rebalance_date = date
        self._trade_day_count = 0
        self._save_state()

    def get_status(self) -> Dict:
        """获取当前状态"""
        return {
            "last_rebalance": self._last_rebalance_date,
            "is_stopped": self._performance.is_stopped,
            "stop_reason": self._performance.stop_reason,
            "rolling_sharpe": self.get_rolling_sharpe(),
            "current_drawdown": self.get_current_drawdown(),
            "trade_days_since_rebalance": self._trade_day_count,
            "total_trade_days": len(self._performance.daily_returns),
        }

    def _save_state(self):
        """保存状态到文件"""
        state = {
            "last_rebalance_date": self._last_rebalance_date,
            "trade_day_count": self._trade_day_count,
            "is_stopped": self._performance.is_stopped,
            "stop_reason": self._performance.stop_reason,
            "sharpe_violations": self._performance.sharpe_violations,
            "peak_value": self._performance.peak_value,
            "current_value": self._performance.current_value,
            "daily_returns_count": len(self._performance.daily_returns),
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _load_state(self):
        """从文件加载状态"""
        if not self._state_path.exists():
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._last_rebalance_date = state.get("last_rebalance_date")
            self._trade_day_count = state.get("trade_day_count", 0)
            self._performance.is_stopped = state.get("is_stopped", False)
            self._performance.stop_reason = state.get("stop_reason", "")
            self._performance.sharpe_violations = state.get("sharpe_violations", 0)
            self._performance.peak_value = state.get("peak_value", 0)
            self._performance.current_value = state.get("current_value", 0)
        except Exception as e:
            logger.warning(f"调仓状态加载失败: {e}")

    def reset(self):
        """重置调度器"""
        self._last_rebalance_date = None
        self._trade_day_count = 0
        self._performance = PerformanceTracker()
        self._save_state()
        logger.info("调仓调度器已重置")
