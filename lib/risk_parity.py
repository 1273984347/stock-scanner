"""等风险贡献（ERC）风险平价组合

每个资产对组合总风险的贡献相等。
求解：min Σ(RC_i - RC_target)^2
其中 RC_i = w_i * (Σw)_i / sqrt(w'Σw)
"""

import logging
import numpy as np
from typing import Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RiskParityResult:
    """风险平价结果"""
    weights: Dict[str, float]  # 资产权重
    risk_contributions: Dict[str, float]  # 风险贡献
    total_risk: float  # 总组合风险（年化波动率）
    n_iterations: int  # 迭代次数


def risk_parity(
    assets: List[str],
    cov_matrix: np.ndarray,
    max_iterations: int = 1000,
    tolerance: float = 1e-8,
) -> RiskParityResult:
    """等风险贡献风险平价

    Args:
        assets: 资产列表
        cov_matrix: 协方差矩阵 (n×n)
        max_iterations: 最大迭代次数
        tolerance: 收敛容差

    Returns:
        RiskParityResult
    """
    n = len(assets)
    cov = np.array(cov_matrix, dtype=float)

    # 初始权重：等权重
    w = np.ones(n) / n

    # 目标风险贡献：每个资产相等
    target_rc = np.ones(n) / n

    # 迭代求解
    for iteration in range(max_iterations):
        # 组合风险
        port_var = w @ cov @ w
        port_vol = np.sqrt(port_var)

        if port_vol < 1e-10:
            break

        # 边际风险贡献
        marginal_risk = cov @ w

        # 风险贡献
        rc = w * marginal_risk / port_vol

        # 归一化风险贡献
        rc_sum = rc.sum()
        if rc_sum > 0:
            rc_normalized = rc / rc_sum
        else:
            rc_normalized = np.ones(n) / n

        # 检查收敛
        error = np.sum((rc_normalized - target_rc) ** 2)
        if error < tolerance:
            break

        # 更新权重：梯度下降
        # w_i_new = w_i * (target_rc_i / rc_i)^0.5
        adjustment = np.sqrt(target_rc / np.maximum(rc_normalized, 1e-10))
        w = w * adjustment

        # 归一化
        w = np.maximum(w, 0)
        w = w / w.sum()

    # 计算最终风险贡献
    port_var = w @ cov @ w
    port_vol = np.sqrt(port_var)
    marginal_risk = cov @ w
    rc = w * marginal_risk / port_vol
    rc_sum = rc.sum()
    if rc_sum > 0:
        rc_normalized = rc / rc_sum
    else:
        rc_normalized = np.ones(n) / n

    weights = {a: float(w[i]) for i, a in enumerate(assets)}
    risk_contributions = {a: float(rc_normalized[i]) for i, a in enumerate(assets)}

    return RiskParityResult(
        weights=weights,
        risk_contributions=risk_contributions,
        total_risk=round(float(port_vol), 6),
        n_iterations=iteration + 1,
    )


def format_risk_parity(result: RiskParityResult) -> str:
    """格式化风险平价结果"""
    lines = [
        "## 等风险贡献（ERC）风险平价",
        f"总组合风险: {result.total_risk:.4f} (年化波动率)",
        f"迭代次数: {result.n_iterations}",
        "",
        "| 资产 | 权重 | 风险贡献 |",
        "|------|------|----------|",
    ]

    for asset in result.weights:
        w = result.weights[asset]
        rc = result.risk_contributions[asset]
        lines.append(f"| {asset} | {w:.2%} | {rc:.2%} |")

    return "\n".join(lines)
