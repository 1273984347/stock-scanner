"""贝叶斯更新器 — 根据交易历史动态更新条件胜率

设计要求（V6）：
- 不断根据市场状态、板块强度、信号类型更新胜率估计
- P(win | market_state, sector_bucket, signal_type)
- 新交易结果自动更新后验概率
"""
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from config import RiskConfig

logger = logging.getLogger(__name__)

# 持久化路径（回测和实盘分离，防止状态污染）
# 注：路径依赖 __file__，保留模块顶层常量（不适合迁到 config.py）
_BAYES_STATE_PATH = Path(__file__).parent.parent.parent / "cache" / "bayesian_state.json"
_BAYES_BACKTEST_PATH = Path(__file__).parent.parent.parent / "cache" / "bayesian_state_backtest.json"

# 贝叶斯平滑与权重调整阈值
# §4.2 A3 迁移 (2026-06-08): 原模块级常量 → config.RiskConfig
_risk_config = RiskConfig()
PRIOR_WIN_RATE: float = _risk_config.prior_win_rate
MIN_SAMPLES: int = _risk_config.bayes_min_samples
HIGH_WIN_THRESHOLD: float = _risk_config.high_win_threshold
LOW_WIN_THRESHOLD: float = _risk_config.low_win_threshold
WEIGHT_SLOPE: float = _risk_config.weight_slope
WEIGHT_CEILING: float = _risk_config.weight_ceiling
WEIGHT_FLOOR: float = _risk_config.weight_floor
MIN_DISPLAY_SAMPLES: int = _risk_config.min_display_samples


@dataclass
class ConditionStats:
    """单个条件组合的统计"""
    wins: int = 0
    losses: int = 0

    @property
    def total(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        if self.total == 0:
            return 0.5  # 无数据时用先验 50%
        return self.wins / self.total

    def update(self, is_win: bool):
        if is_win:
            self.wins += 1
        else:
            self.losses += 1


class BayesianUpdater:
    """贝叶斯胜率更新器

    用法：
        updater = BayesianUpdater()
        # 获取条件胜率
        rate = updater.get_posterior("TREND_UP", "high", "donchian_breakout")
        # 更新交易结果
        updater.update("TREND_UP", "high", "donchian_breakout", is_win=True)
        # 保存状态
        updater.save()
    """

    def __init__(self, state_path: Path = None, mode: str = "live"):
        """初始化贝叶斯胜率更新器

        Args:
            state_path: 自定义状态文件路径
            mode: "live"（实盘）或 "backtest"（回测），使用不同的状态文件
        """
        if state_path:
            self._path = state_path
        elif mode == "backtest":
            self._path = _BAYES_BACKTEST_PATH
        else:
            self._path = _BAYES_STATE_PATH
        # key: "market_state|sector_bucket|signal_type" -> ConditionStats
        self._stats: Dict[str, ConditionStats] = {}
        # 先验：无数据时的默认胜率
        self._prior_win_rate = PRIOR_WIN_RATE
        # 最小样本量：低于此数量时使用先验与观测的加权
        self._min_samples = MIN_SAMPLES
        self._load()

    def _make_key(self, market_state: str, sector_bucket: str, signal_type: str) -> str:
        return f"{market_state}|{sector_bucket}|{signal_type}"

    def get_posterior(self, market_state: str, sector_bucket: str,
                      signal_type: str) -> float:
        """获取后验胜率 P(win | conditions)

        贝叶斯平滑：样本不足时向先验收缩。
        posterior = (wins + prior * k) / (total + k)
        其中 k = min_samples，控制收缩强度。
        """
        key = self._make_key(market_state, sector_bucket, signal_type)
        stats = self._stats.get(key)
        if stats is None or stats.total == 0:
            return self._prior_win_rate

        # 贝叶斯平滑（Beta-Binomial 共轭）
        k = self._min_samples
        posterior = (stats.wins + self._prior_win_rate * k) / (stats.total + k)
        return round(posterior, 4)

    def update(self, market_state: str, sector_bucket: str,
               signal_type: str, is_win: bool):
        """更新交易结果"""
        key = self._make_key(market_state, sector_bucket, signal_type)
        if key not in self._stats:
            self._stats[key] = ConditionStats()
        self._stats[key].update(is_win)

    def get_stats_summary(self) -> Dict[str, dict]:
        """获取所有条件的统计摘要"""
        summary = {}
        for key, stats in self._stats.items():
            if stats.total >= MIN_DISPLAY_SAMPLES:  # 至少 N 笔才显示
                summary[key] = {
                    "wins": stats.wins,
                    "losses": stats.losses,
                    "total": stats.total,
                    "win_rate": round(stats.win_rate, 3),
                    "posterior": round(self.get_posterior(*key.split("|")), 3),
                }
        return summary

    def get_weight_adjustment(self, market_state: str, sector_bucket: str,
                              signal_type: str) -> float:
        """根据后验胜率返回信号权重调整系数

        胜率 > 60%: 权重上调（最高 1.5x）
        胜率 40-60%: 权重不变（1.0x）
        胜率 < 40%: 权重下调（最低 0.5x）
        """
        posterior = self.get_posterior(market_state, sector_bucket, signal_type)
        if posterior >= HIGH_WIN_THRESHOLD:
            return min(1.0 + (posterior - HIGH_WIN_THRESHOLD) * WEIGHT_SLOPE, WEIGHT_CEILING)
        elif posterior <= LOW_WIN_THRESHOLD:
            return max(1.0 - (LOW_WIN_THRESHOLD - posterior) * WEIGHT_SLOPE, WEIGHT_FLOOR)
        return 1.0

    def save(self):
        """持久化到 JSON"""
        data = {}
        for key, stats in self._stats.items():
            data[key] = {"wins": stats.wins, "losses": stats.losses}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self):
        """从 JSON 加载"""
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, vals in data.items():
                self._stats[key] = ConditionStats(
                    wins=vals.get("wins", 0),
                    losses=vals.get("losses", 0),
                )
        except Exception as e:
            logger.warning(f"贝叶斯状态加载失败: {e}")
