"""资本配置器 —— V9 核心模块

核心思想：不是配置股票，而是配置 Alpha。

根据 Alpha 工厂的输出，动态分配资金给不同的 Alpha 类型。
- 有效 Alpha → 增加资金
- 衰退 Alpha → 减少资金
- 失效 Alpha → 停止资金
"""
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AllocationResult:
    """单个 Alpha 的资金分配结果"""
    alpha_type: str
    weight: float           # 资金权重 0-1
    risk_budget: float      # 风险预算
    max_position: float     # 最大仓位
    reason: str


class CapitalAllocator:
    """资本配置器 —— 基于 Alpha 动态分配资金

    用法：
        allocator = CapitalAllocator()
        allocations = allocator.allocate(
            alpha_weights={"trend": 0.6, "sentiment": 0.4},
            total_capital=1_000_000,
            regime="TREND_UP",
        )
    """

    # 市场状态 → 总风险预算系数
    REGIME_RISK_BUDGET = {
        "TREND_UP": 0.80,        # 趋势市场：高风险预算
        "LOW_VOLATILITY": 0.70,
        "MEAN_REVERSION": 0.50,  # 震荡市场：中等风险预算
        "HIGH_VOLATILITY": 0.30, # 高波动：低风险预算
        "TREND_DOWN": 0.10,      # 下跌：极低风险预算
        "RISK_OFF": 0.00,        # 极端：零风险预算
    }

    # 单个 Alpha 最大仓位
    MAX_SINGLE_ALPHA = 0.40
    # 最小仓位（低于此值不分配）
    MIN_ALLOCATION = 0.05

    def allocate(
        self,
        alpha_weights: Dict[str, float],
        total_capital: float,
        regime: str = "MEAN_REVERSION",
        regime_confidence: float = 0.5,
    ) -> List[AllocationResult]:
        """执行资本配置

        Args:
            alpha_weights: 各 Alpha 类型的权重（来自 AlphaFeedback.get_alpha_weights()）
            total_capital: 总资金
            regime: 市场状态
            regime_confidence: 市场状态置信度

        Returns:
            各 Alpha 的资金分配结果
        """
        if not alpha_weights or total_capital <= 0:
            return []

        risk_budget_coeff = self.REGIME_RISK_BUDGET.get(regime, 0.50)
        total_risk_budget = risk_budget_coeff * regime_confidence

        allocations = []
        for alpha_type, weight in alpha_weights.items():
            if weight < self.MIN_ALLOCATION:
                continue

            # 风险预算 = 总预算 × Alpha 权重
            alpha_risk = total_risk_budget * weight

            # 最大仓位
            max_position = min(weight * total_risk_budget, self.MAX_SINGLE_ALPHA)

            reason = f"regime={regime}, budget={total_risk_budget:.0%}, alpha_weight={weight:.0%}"

            allocations.append(AllocationResult(
                alpha_type=alpha_type,
                weight=round(weight, 4),
                risk_budget=round(alpha_risk, 4),
                max_position=round(max_position, 4),
                reason=reason,
            ))

        # 归一化权重
        total_weight = sum(a.weight for a in allocations)
        if total_weight > 0:
            for a in allocations:
                a.weight = round(a.weight / total_weight, 4)

        return allocations

    def allocate_with_feedback(
        self,
        alpha_feedback,
        total_capital: float,
        regime: str = "MEAN_REVERSION",
        regime_confidence: float = 0.5,
    ) -> List[AllocationResult]:
        """从 AlphaFeedback 对象直接分配

        Args:
            alpha_feedback: AlphaFeedback 实例
            total_capital: 总资金
            regime: 市场状态
            regime_confidence: 市场状态置信度
        """
        weights = alpha_feedback.get_alpha_weights()
        return self.allocate(weights, total_capital, regime, regime_confidence)

    def allocate_with_factory(
        self,
        alpha_factory,
        total_capital: float,
        regime: str = "MEAN_REVERSION",
        regime_confidence: float = 0.5,
    ) -> List[AllocationResult]:
        """从 AlphaFactory 对象直接分配

        只分配 lifecycle=effective 或 discovered 的 Alpha。
        """
        active_alphas = alpha_factory.get_active_alphas()
        if not active_alphas:
            return []

        # 按 win_rate 构建权重
        raw_weights = {}
        for record in active_alphas:
            if record.win_rate > 0.3:
                raw_weights[record.alpha_id] = record.win_rate

        total = sum(raw_weights.values())
        if total <= 0:
            return {}

        weights = {k: v / total for k, v in raw_weights.items()}
        return self.allocate(weights, total_capital, regime, regime_confidence)

    def get_report(self, allocations: List[AllocationResult]) -> str:
        """返回人类可读的配置报告"""
        lines = ["=== 资本配置报告 ==="]
        for a in allocations:
            lines.append(
                f"  {a.alpha_type:20s} | 权重 {a.weight:.0%} | "
                f"风险预算 {a.risk_budget:.0%} | "
                f"最大仓位 {a.max_position:.0%}"
            )
        return "\n".join(lines)
