"""Alpha 工厂 —— V8 核心模块

自动发现、评估、管理 Alpha 生命周期。

生命周期状态机：
  discovered → effective → crowded → decaying → dead
                    ↑                        |
                    └────────────────────────┘ (re-evaluate)
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import numpy as np

logger = logging.getLogger(__name__)

_PERSIST_PATH = Path(__file__).parent.parent / "cache" / "alpha_factory.json"

# 生命周期状态
LIFECYCLE_DISCOVERED = "discovered"
LIFECYCLE_EFFECTIVE = "effective"
LIFECYCLE_CROWDED = "crowded"
LIFECYCLE_DECAYING = "decaying"
LIFECYCLE_DEAD = "dead"

VALID_LIFECYCLES = [LIFECYCLE_DISCOVERED, LIFECYCLE_EFFECTIVE,
                    LIFECYCLE_CROWDED, LIFECYCLE_DECAYING, LIFECYCLE_DEAD]


@dataclass
class AlphaRecord:
    """单个 Alpha 的完整记录"""
    alpha_id: str = ""
    name: str = ""
    factor_name: str = ""
    ic: float = 0.0
    icir: float = 0.0
    win_rate: float = 0.0
    sharpe: float = 0.0
    lifecycle: str = LIFECYCLE_DISCOVERED
    discovered_date: str = ""
    last_evaluated: str = ""
    trade_count: int = 0
    pnl_history: List[float] = field(default_factory=list)

    @property
    def avg_pnl(self) -> float:
        return float(np.mean(self.pnl_history)) if self.pnl_history else 0.0

    @property
    def recent_win_rate(self, lookback: int = 20) -> float:
        if not self.pnl_history:
            return 0.5
        recent = self.pnl_history[-lookback:]
        return sum(1 for p in recent if p > 0) / len(recent)


@dataclass
class AlphaEvaluation:
    """Alpha 评估结果"""
    alpha_id: str
    ic: float
    icir: float
    win_rate: float
    sharpe: float
    trade_count: int
    lifecycle: str
    recommendation: str  # keep / reduce / drop


class AlphaFactory:
    """Alpha 工厂 —— 自动发现、评估、管理 Alpha 生命周期

    用法：
        factory = AlphaFactory()
        alpha_id = factory.discover("momentum_20", factor_values, forward_returns)
        factory.record_trade(alpha_id, pnl_pct=5.2)
        factory.update_lifecycle()
        report = factory.get_report()
    """

    # IC 阈值：高于此值认为 Alpha 有效
    IC_THRESHOLD = 0.03
    # ICIR 阈值：高于此值认为 Alpha 稳定
    ICIR_THRESHOLD = 0.5
    # 衰退阈值：近 N 笔胜率低于此值认为衰退
    DECAY_WIN_RATE = 0.40
    # 死亡阈值：近 N 笔胜率低于此值认为失效
    DEAD_WIN_RATE = 0.30
    # 最小样本量
    MIN_TRADES = 10

    def __init__(self, persist_path: Path = None):
        self._path = persist_path or _PERSIST_PATH
        self._alphas: Dict[str, AlphaRecord] = {}
        self._load()

    def discover(self, factor_name: str,
                 factor_values=None,
                 forward_returns=None,
                 name: str = None) -> Optional[str]:
        """发现新 Alpha

        条件：IC > 0.03 且 p < 0.05

        Args:
            factor_name: 因子名称（如 "momentum_20", "rsi_14"）
            factor_values: 因子值 Series（可选，用于计算 IC）
            forward_returns: 前向收益 Series（可选）
            name: Alpha 可读名称（可选）

        Returns:
            alpha_id 或 None（未达到发现阈值）
        """
        alpha_id = f"alpha_{factor_name}"

        # 如果已存在，返回现有 ID
        if alpha_id in self._alphas:
            return alpha_id

        # 计算 IC（如果有数据）
        ic = 0.0
        icir = 0.0
        if factor_values is not None and forward_returns is not None:
            try:
                from lib.alpha import AlphaSynthesizer
                ic_result = AlphaSynthesizer.compute_ic(factor_values, forward_returns)
                ic = ic_result.get("rank_ic", 0.0) or 0.0
                # ICIR 需要滚动 IC，简化处理
                icir = abs(ic) * 10  # 粗略估计
            except Exception:
                pass

        # 检查发现阈值
        if abs(ic) < self.IC_THRESHOLD and factor_values is not None:
            logger.debug(f"Alpha [{factor_name}] IC={ic:.4f} 未达阈值 {self.IC_THRESHOLD}")
            return None

        record = AlphaRecord(
            alpha_id=alpha_id,
            name=name or factor_name,
            factor_name=factor_name,
            ic=round(ic, 4),
            icir=round(icir, 4),
            lifecycle=LIFECYCLE_DISCOVERED,
            discovered_date=datetime.now().strftime("%Y-%m-%d"),
            last_evaluated=datetime.now().strftime("%Y-%m-%d"),
        )
        self._alphas[alpha_id] = record
        logger.info(f"发现新 Alpha [{alpha_id}]: IC={ic:.4f}")
        return alpha_id

    def record_trade(self, alpha_id: str, pnl_pct: float):
        """记录交易结果"""
        if alpha_id not in self._alphas:
            logger.warning(f"未知 Alpha: {alpha_id}")
            return

        record = self._alphas[alpha_id]
        record.trade_count += 1
        record.pnl_history.append(pnl_pct)
        # 只保留最近 200 笔
        if len(record.pnl_history) > 200:
            record.pnl_history = record.pnl_history[-200:]

        # 更新统计
        wins = sum(1 for p in record.pnl_history if p > 0)
        record.win_rate = round(wins / record.trade_count, 3) if record.trade_count > 0 else 0
        if len(record.pnl_history) >= 2:
            record.sharpe = round(
                float(np.mean(record.pnl_history) / (np.std(record.pnl_history) + 1e-10)), 2
            )

    def evaluate(self, alpha_id: str) -> AlphaEvaluation:
        """评估 Alpha 有效性"""
        record = self._alphas.get(alpha_id)
        if record is None:
            return AlphaEvaluation(
                alpha_id=alpha_id, ic=0, icir=0, win_rate=0,
                sharpe=0, trade_count=0, lifecycle="unknown",
                recommendation="drop",
            )

        # 确定建议
        if record.trade_count < self.MIN_TRADES:
            recommendation = "keep"  # 样本不足，继续观察
        elif record.win_rate >= 0.55:
            recommendation = "keep"
        elif record.win_rate >= 0.45:
            recommendation = "reduce"
        else:
            recommendation = "drop"

        return AlphaEvaluation(
            alpha_id=alpha_id,
            ic=record.ic,
            icir=record.icir,
            win_rate=record.win_rate,
            sharpe=record.sharpe,
            trade_count=record.trade_count,
            lifecycle=record.lifecycle,
            recommendation=recommendation,
        )

    def update_lifecycle(self) -> List[str]:
        """更新所有 Alpha 的生命周期状态

        Returns:
            状态发生变化的 Alpha ID 列表
        """
        changed = []
        for alpha_id, record in self._alphas.items():
            old_lifecycle = record.lifecycle

            if record.trade_count < self.MIN_TRADES:
                # 样本不足，保持 discovered
                continue

            recent = record.pnl_history[-20:] if len(record.pnl_history) >= 20 else record.pnl_history
            recent_wins = sum(1 for p in recent if p > 0)
            recent_win_rate = recent_wins / len(recent) if recent else 0

            # 状态转换逻辑
            if recent_win_rate < self.DEAD_WIN_RATE:
                record.lifecycle = LIFECYCLE_DEAD
            elif recent_win_rate < self.DECAY_WIN_RATE:
                record.lifecycle = LIFECYCLE_DECAYING
            elif record.win_rate > 0.55 and record.sharpe > 0.5:
                if old_lifecycle in (LIFECYCLE_DISCOVERED, LIFECYCLE_DECAYING):
                    record.lifecycle = LIFECYCLE_EFFECTIVE
                elif old_lifecycle == LIFECYCLE_EFFECTIVE:
                    # 检查是否拥挤（胜率下降但仍高于阈值）
                    if recent_win_rate < record.win_rate * 0.85:
                        record.lifecycle = LIFECYCLE_CROWDED
                    else:
                        record.lifecycle = LIFECYCLE_EFFECTIVE
                elif old_lifecycle == LIFECYCLE_CROWDED:
                    if recent_win_rate >= record.win_rate * 0.95:
                        record.lifecycle = LIFECYCLE_EFFECTIVE  # 恢复
            elif old_lifecycle == LIFECYCLE_EFFECTIVE and recent_win_rate < 0.50:
                record.lifecycle = LIFECYCLE_CROWDED

            if record.lifecycle != old_lifecycle:
                changed.append(alpha_id)
                logger.info(
                    f"Alpha [{alpha_id}] 生命周期: {old_lifecycle} → {record.lifecycle}"
                )

            record.last_evaluated = datetime.now().strftime("%Y-%m-%d")

        return changed

    def get_active_alphas(self) -> List[AlphaRecord]:
        """返回当前有效的 Alpha（effective 或 discovered）"""
        return [
            r for r in self._alphas.values()
            if r.lifecycle in (LIFECYCLE_EFFECTIVE, LIFECYCLE_DISCOVERED)
        ]

    def get_report(self) -> dict:
        """返回 Alpha 工厂报告"""
        report = {
            "total_alphas": len(self._alphas),
            "by_lifecycle": {},
            "alphas": {},
        }

        for lc in VALID_LIFECYCLES:
            report["by_lifecycle"][lc] = sum(
                1 for r in self._alphas.values() if r.lifecycle == lc
            )

        for alpha_id, record in self._alphas.items():
            report["alphas"][alpha_id] = {
                "name": record.name,
                "factor_name": record.factor_name,
                "ic": record.ic,
                "icir": record.icir,
                "win_rate": record.win_rate,
                "sharpe": record.sharpe,
                "lifecycle": record.lifecycle,
                "trade_count": record.trade_count,
                "avg_pnl": round(record.avg_pnl, 2),
                "discovered_date": record.discovered_date,
                "last_evaluated": record.last_evaluated,
            }

        return report

    def get_summary(self) -> str:
        """返回人类可读的摘要"""
        report = self.get_report()
        lines = ["=== Alpha 工厂报告 ==="]
        lines.append(f"总计: {report['total_alphas']} 个 Alpha")
        lines.append(f"  有效: {report['by_lifecycle'].get('effective', 0)}")
        lines.append(f"  发现中: {report['by_lifecycle'].get('discovered', 0)}")
        lines.append(f"  拥挤: {report['by_lifecycle'].get('crowded', 0)}")
        lines.append(f"  衰退: {report['by_lifecycle'].get('decaying', 0)}")
        lines.append(f"  失效: {report['by_lifecycle'].get('dead', 0)}")
        lines.append("")
        for alpha_id, info in report["alphas"].items():
            lines.append(
                f"  {info['name']:20s} | {info['lifecycle']:12s} | "
                f"IC={info['ic']:.3f} | 胜率={info['win_rate']:.0%} | "
                f"交易={info['trade_count']}笔"
            )
        return "\n".join(lines)

    def save(self):
        """持久化到 JSON"""
        data = {
            alpha_id: {
                "alpha_id": r.alpha_id,
                "name": r.name,
                "factor_name": r.factor_name,
                "ic": r.ic,
                "icir": r.icir,
                "win_rate": r.win_rate,
                "sharpe": r.sharpe,
                "lifecycle": r.lifecycle,
                "discovered_date": r.discovered_date,
                "last_evaluated": r.last_evaluated,
                "trade_count": r.trade_count,
                "pnl_history": r.pnl_history[-200:],
            }
            for alpha_id, r in self._alphas.items()
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
            for alpha_id, v in data.items():
                self._alphas[alpha_id] = AlphaRecord(
                    alpha_id=v.get("alpha_id", alpha_id),
                    name=v.get("name", ""),
                    factor_name=v.get("factor_name", ""),
                    ic=v.get("ic", 0),
                    icir=v.get("icir", 0),
                    win_rate=v.get("win_rate", 0),
                    sharpe=v.get("sharpe", 0),
                    lifecycle=v.get("lifecycle", LIFECYCLE_DISCOVERED),
                    discovered_date=v.get("discovered_date", ""),
                    last_evaluated=v.get("last_evaluated", ""),
                    trade_count=v.get("trade_count", 0),
                    pnl_history=v.get("pnl_history", []),
                )
        except Exception as e:
            logger.warning(f"AlphaFactory 加载失败: {e}")
