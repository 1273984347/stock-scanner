"""Alpha 反馈闭环 —— V7 核心模块

功能：
1. 记录每笔交易的 Alpha 来源和盈亏
2. 基于贝叶斯更新器动态调整各 Alpha 权重
3. 检测正在衰退的 Alpha
4. 持久化到 JSON
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
import numpy as np

logger = logging.getLogger(__name__)

_PERSIST_PATH = Path(__file__).parent.parent / "cache" / "alpha_feedback.json"


@dataclass
class AlphaStats:
    """单个 Alpha 类型的统计"""
    alpha_type: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    pnl_history: List[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def avg_win(self) -> float:
        wins = [p for p in self.pnl_history if p > 0]
        return float(np.mean(wins)) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [abs(p) for p in self.pnl_history if p <= 0]
        return float(np.mean(losses)) if losses else 0.0

    @property
    def profit_factor(self) -> float:
        gross_win = sum(p for p in self.pnl_history if p > 0)
        gross_loss = abs(sum(p for p in self.pnl_history if p <= 0))
        return gross_win / gross_loss if gross_loss > 0 else float('inf')

    @property
    def sharpe(self) -> float:
        if len(self.pnl_history) < 2:
            return 0.0
        return float(np.mean(self.pnl_history) / (np.std(self.pnl_history) + 1e-10))


class AlphaFeedback:
    """Alpha 反馈闭环管理器

    用法：
        fb = AlphaFeedback()
        fb.record_trade("trend", 5.2, "TREND_UP", "AI")
        fb.record_trade("trend", -2.1, "TREND_UP", "AI")
        weights = fb.get_alpha_weights()
        report = fb.get_alpha_report()
    """

    def __init__(self, persist_path: Path = None):
        self._path = persist_path or _PERSIST_PATH
        self._stats: Dict[str, AlphaStats] = {}
        self._trade_log: List[dict] = []
        self._previous_weights: Dict[str, float] = {}
        self._load()

    def record_trade(self, alpha_type: str, pnl_pct: float,
                     regime: str = "", sector: str = ""):
        """记录交易结果

        Args:
            alpha_type: Alpha 来源类型（trend/sector_rotation/sentiment/mean_reversion/event_driven）
            pnl_pct: 盈亏百分比（如 5.2 表示 +5.2%）
            regime: 入场时市场状态
            sector: 入场时板块
        """
        if alpha_type not in self._stats:
            self._stats[alpha_type] = AlphaStats(alpha_type=alpha_type)

        stats = self._stats[alpha_type]
        stats.total_trades += 1
        stats.total_pnl += pnl_pct
        stats.pnl_history.append(pnl_pct)
        if pnl_pct > 0:
            stats.wins += 1
        else:
            stats.losses += 1

        # 只保留最近 100 笔
        if len(stats.pnl_history) > 100:
            stats.pnl_history = stats.pnl_history[-100:]

        self._trade_log.append({
            "alpha_type": alpha_type,
            "pnl_pct": pnl_pct,
            "regime": regime,
            "sector": sector,
        })

    def get_alpha_weights(self) -> Dict[str, float]:
        """返回各 Alpha 类型的动态权重（基于后验胜率归一化）

        权重计算：
        - 取各 Alpha 的 win_rate
        - 低于 30% 的设为 0（淘汰）
        - 归一化到和为 1
        """
        if not self._stats:
            return {}

        raw_weights = {}
        for alpha_type, stats in self._stats.items():
            if stats.total_trades < 3:
                # 样本不足，给默认权重
                raw_weights[alpha_type] = 0.5
            elif stats.win_rate < 0.30:
                # 胜率过低，淘汰
                raw_weights[alpha_type] = 0.0
            else:
                raw_weights[alpha_type] = stats.win_rate

        total = sum(raw_weights.values())
        if total <= 0:
            # P0-3 修复（2026-06-05）+ dim-5 P1-2 + dim-2 P0-3 三方共识：
            # 旧逻辑：返回 {k: 1.0 / len(raw_weights)}（等权启用，方向相反！）
            # 新逻辑：返回 {}（空字典 → kill switch）让上层（position_sizer / risk_manager）
            #         显式响应"全市场关仓"，而不是悄悄回退到等权下注。
            # 同时打 warning log 让运维可见
            logger.warning(
                f"Alpha kill switch 触发: 所有 {len(raw_weights)} 个 alpha 胜率<30%，"
                f"建议全市场关仓（position_sizer 应将仓位降至 0）"
            )
            self._previous_weights = {}  # 清空历史权重
            return {}

        weights = {k: round(v / total, 4) for k, v in raw_weights.items()}

        # 检测权重变化超过 20% 的情况
        for alpha_type, weight in weights.items():
            prev = self._previous_weights.get(alpha_type, weight)
            if abs(weight - prev) > 0.20:
                logger.warning(
                    f"Alpha权重剧变 [{alpha_type}]: {prev:.2%} → {weight:.2%}"
                )

        self._previous_weights = weights.copy()
        return weights

    def get_alpha_report(self) -> Dict[str, dict]:
        """返回 Alpha 统计报告"""
        report = {}
        for alpha_type, stats in self._stats.items():
            report[alpha_type] = {
                "total_trades": stats.total_trades,
                "wins": stats.wins,
                "losses": stats.losses,
                "win_rate": round(stats.win_rate, 3),
                "avg_pnl": round(stats.avg_pnl, 2),
                "avg_win": round(stats.avg_win, 2),
                "avg_loss": round(stats.avg_loss, 2),
                "profit_factor": round(stats.profit_factor, 2),
                "sharpe": round(stats.sharpe, 2),
            }
        return report

    def detect_alpha_decay(self, lookback: int = 20) -> List[str]:
        """检测正在衰退的 Alpha（最近 N 笔胜率 < 40%）

        Returns:
            衰退的 Alpha 类型列表
        """
        decayed = []
        for alpha_type, stats in self._stats.items():
            if stats.total_trades < lookback:
                continue
            recent = stats.pnl_history[-lookback:]
            recent_wins = sum(1 for p in recent if p > 0)
            recent_win_rate = recent_wins / len(recent)
            if recent_win_rate < 0.40:
                decayed.append(alpha_type)
                logger.warning(
                    f"Alpha衰退 [{alpha_type}]: 近{lookback}笔胜率仅 {recent_win_rate:.0%}"
                )
        return decayed

    def get_summary(self) -> str:
        """返回人类可读的摘要"""
        weights = self.get_alpha_weights()
        report = self.get_alpha_report()
        lines = ["=== Alpha 反馈报告 ==="]
        for alpha_type in sorted(report.keys()):
            r = report[alpha_type]
            w = weights.get(alpha_type, 0)
            lines.append(
                f"  {alpha_type:20s} | 胜率 {r['win_rate']:.0%} | "
                f"盈亏比 {r['profit_factor']:.1f} | "
                f"权重 {w:.0%} | 交易 {r['total_trades']}笔"
            )
        decayed = self.detect_alpha_decay()
        if decayed:
            lines.append(f"  ⚠ 衰退Alpha: {', '.join(decayed)}")
        return "\n".join(lines)

    def save(self):
        """持久化到 JSON"""
        data = {
            "stats": {
                k: {
                    "alpha_type": v.alpha_type,
                    "total_trades": v.total_trades,
                    "wins": v.wins,
                    "losses": v.losses,
                    "total_pnl": v.total_pnl,
                    "pnl_history": v.pnl_history[-100:],
                }
                for k, v in self._stats.items()
            },
            "previous_weights": self._previous_weights,
        }
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
            for k, v in data.get("stats", {}).items():
                self._stats[k] = AlphaStats(
                    alpha_type=v.get("alpha_type", k),
                    total_trades=v.get("total_trades", 0),
                    wins=v.get("wins", 0),
                    losses=v.get("losses", 0),
                    total_pnl=v.get("total_pnl", 0),
                    pnl_history=v.get("pnl_history", []),
                )
            self._previous_weights = data.get("previous_weights", {})
        except Exception as e:
            logger.warning(f"AlphaFeedback 加载失败: {e}")
