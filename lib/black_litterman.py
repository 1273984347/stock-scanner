"""Black-Litterman 模型 -- 结合市场均衡和主观观点的组合优化

核心公式：
- 市场均衡收益: Pi = delta * Sigma * w_mkt
- 后验收益: E[R] = [(tau*Sigma)^{-1} + P'*Omega^{-1}*P]^{-1} * [(tau*Sigma)^{-1}*Pi + P'*Omega^{-1}*Q]
- 后验权重: w = (delta*Sigma)^{-1} * E[R]
"""

import logging
import numpy as np
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BLView:
    """投资者观点"""
    assets: List[str]  # 涉及的资产
    weights: List[float]  # 观点权重（相对收益方向）
    expected_return: float  # 预期超额收益
    confidence: float = 0.5  # 置信度 (0-1)


@dataclass
class BLResult:
    """Black-Litterman 结果"""
    posterior_returns: Dict[str, float]  # 后验预期收益
    posterior_weights: Dict[str, float]  # 后验权重
    equilibrium_returns: Dict[str, float]  # 均衡收益
    view_returns: Dict[str, float]  # 观点隐含收益


def black_litterman(
    assets: List[str],
    cov_matrix: np.ndarray,
    market_weights: Dict[str, float],
    views: List[BLView] = None,
    risk_aversion: float = 2.5,
    tau: float = 0.05,
) -> BLResult:
    """Black-Litterman 模型

    Args:
        assets: 资产列表
        cov_matrix: 协方差矩阵 (n x n)
        market_weights: 市场均衡权重 {asset: weight}
        views: 投资者观点列表
        risk_aversion: 风险厌恶系数（默认 2.5）
        tau: 不确定性缩放因子（默认 0.05）

    Returns:
        BLResult
    """
    n = len(assets)
    cov = np.array(cov_matrix, dtype=float)

    # 市场均衡权重向量
    w_mkt = np.array([market_weights.get(a, 1.0 / n) for a in assets])
    w_mkt = w_mkt / w_mkt.sum()  # 归一化

    # 均衡收益: Pi = delta * Sigma * w_mkt
    pi = risk_aversion * cov @ w_mkt

    equilibrium_returns = {a: float(pi[i]) for i, a in enumerate(assets)}

    if not views or len(views) == 0:
        # 无观点时，均衡收益即后验收益
        weights = {a: float(w_mkt[i]) for i, a in enumerate(assets)}
        return BLResult(
            posterior_returns=equilibrium_returns,
            posterior_weights=weights,
            equilibrium_returns=equilibrium_returns,
            view_returns={},
        )

    # 构建观点矩阵 P 和观点向量 Q
    k = len(views)
    P = np.zeros((k, n))
    Q = np.zeros(k)
    omega_diag = np.zeros(k)

    asset_idx = {a: i for i, a in enumerate(assets)}

    for j, view in enumerate(views):
        for asset, weight in zip(view.assets, view.weights):
            if asset in asset_idx:
                P[j, asset_idx[asset]] = weight
        Q[j] = view.expected_return
        # 观点不确定性: Omega_jj = (1/confidence - 1) * P_j * tau*Sigma * P_j'
        p_sigma = P[j] @ (tau * cov) @ P[j]
        omega_diag[j] = p_sigma * (1.0 / max(view.confidence, 0.01) - 1)

    Omega = np.diag(omega_diag)

    # 后验收益: E[R] = [(tau*Sigma)^{-1} + P'*Omega^{-1}*P]^{-1} * [(tau*Sigma)^{-1}*Pi + P'*Omega^{-1}*Q]
    tau_cov_inv = np.linalg.inv(tau * cov)
    omega_inv = np.linalg.inv(Omega)

    M = np.linalg.inv(tau_cov_inv + P.T @ omega_inv @ P)
    posterior_mean = M @ (tau_cov_inv @ pi + P.T @ omega_inv @ Q)

    # 后验权重: w = (delta*Sigma)^{-1} * E[R]
    posterior_weights_raw = np.linalg.inv(risk_aversion * cov) @ posterior_mean
    posterior_weights_raw = np.maximum(posterior_weights_raw, 0)  # 非负约束
    if posterior_weights_raw.sum() > 0:
        posterior_weights_raw = posterior_weights_raw / posterior_weights_raw.sum()

    posterior_returns = {a: float(posterior_mean[i]) for i, a in enumerate(assets)}
    posterior_weights = {a: float(posterior_weights_raw[i]) for i, a in enumerate(assets)}
    view_returns = {f"view_{j}": float(Q[j]) for j in range(k)}

    return BLResult(
        posterior_returns=posterior_returns,
        posterior_weights=posterior_weights,
        equilibrium_returns=equilibrium_returns,
        view_returns=view_returns,
    )


def format_bl_result(result: BLResult) -> str:
    """格式化 BL 结果"""
    lines = ["## Black-Litterman 优化结果", ""]

    lines.append("| 资产 | 均衡收益 | 后验收益 | 后验权重 |")
    lines.append("|------|----------|----------|----------|")

    for asset in result.posterior_returns:
        eq = result.equilibrium_returns.get(asset, 0)
        post = result.posterior_returns.get(asset, 0)
        weight = result.posterior_weights.get(asset, 0)
        lines.append(f"| {asset} | {eq:.2%} | {post:.2%} | {weight:.2%} |")

    return "\n".join(lines)
