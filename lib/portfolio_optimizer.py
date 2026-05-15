"""组合优化器 —— 均值方差优化 + 行业约束 + 换手惩罚

核心功能：
1. 从候选股评分推导预期收益
2. 用历史协方差矩阵建模风险
3. 行业暴露上限约束
4. 换手率惩罚（减少交易成本）
5. 风险平价备选方案
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class OptimizerConfig:
    """优化器配置"""
    max_position_pct: float = 0.15       # 单股最大仓位
    max_sector_pct: float = 0.30         # 单行业最大仓位
    max_positions: int = 10              # 最大持仓数
    risk_aversion: float = 2.0           # 风险厌恶系数
    turnover_penalty: float = 0.005      # 换手惩罚（每换1%仓位扣0.5bp）
    min_weight: float = 0.02             # 最小仓位（低于此视为0）
    target_vol: float = 0.15             # 目标年化波动率
    score_to_return: float = 0.001       # 评分→预期日收益率映射系数


@dataclass
class OptimizedPortfolio:
    """优化结果"""
    weights: Dict[str, float]            # {code: weight}
    expected_return: float               # 预期年化收益
    expected_vol: float                  # 预期年化波动
    sharpe_ratio: float                  # 预期夏普
    sector_exposure: Dict[str, float]    # {sector: weight}
    turnover: float                      # 换手率
    n_positions: int                     # 持仓数
    details: List[Dict] = field(default_factory=list)  # 每股详情


class PortfolioOptimizer:
    """组合优化器"""

    def __init__(self, config: OptimizerConfig = None):
        self.config = config or OptimizerConfig()

    def optimize(
        self,
        candidates: List[Dict],
        covariance_matrix: Optional[pd.DataFrame] = None,
        current_weights: Optional[Dict[str, float]] = None,
        sector_map: Optional[Dict[str, str]] = None,
    ) -> OptimizedPortfolio:
        """组合优化主入口

        Args:
            candidates: 候选股列表，每项需含 code, score, name
            covariance_matrix: 股票间协方差矩阵（code x code）
            current_weights: 当前持仓权重 {code: weight}
            sector_map: 股票→行业映射 {code: sector}

        Returns:
            OptimizedPortfolio
        """
        cfg = self.config

        # 1. 筛选有效候选
        valid = [c for c in candidates
                 if c.get("score") is not None and c.get("code")]
        if not valid:
            return OptimizedPortfolio(
                weights={}, expected_return=0, expected_vol=0,
                sharpe_ratio=0, sector_exposure={}, turnover=0, n_positions=0)

        # 2. 按评分排序，取 top N
        valid.sort(key=lambda x: x.get("score", 0), reverse=True)
        valid = valid[:cfg.max_positions * 2]  # 多取一些用于优化

        codes = [c["code"] for c in valid]
        scores = np.array([c.get("score", 0) for c in valid])

        # 3. 预期收益：评分 → 预期年化收益
        expected_returns = scores * cfg.score_to_return * 252  # 年化

        # 4. 协方差矩阵（无则用对角矩阵近似）
        n = len(codes)
        if covariance_matrix is not None and not covariance_matrix.empty:
            # 对齐行列
            common = [c for c in codes if c in covariance_matrix.columns]
            if len(common) >= 2:
                cov = covariance_matrix.loc[common, common].values * 252  # 年化
                codes = common
                expected_returns = expected_returns[:len(common)]
                valid = valid[:len(common)]
            else:
                cov = np.diag(np.ones(n) * (cfg.target_vol ** 2))
        else:
            # 用波动率估计构建对角协方差
            vols = []
            for c in valid:
                vol = c.get("volatility", cfg.target_vol)
                vols.append(vol)
            cov = np.diag(np.array(vols) ** 2) * 252

        # 5. 均值方差优化
        weights = self._mean_variance_optimize(
            expected_returns, cov,
            max_weight=cfg.max_position_pct,
            risk_aversion=cfg.risk_aversion,
        )

        # 6. 行业约束：超限行业削减
        if sector_map:
            weights = self._apply_sector_constraints(
                weights, codes, sector_map, cfg.max_sector_pct)

        # 7. 换手惩罚：减少不必要的调仓
        if current_weights:
            weights = self._apply_turnover_penalty(
                weights, codes, current_weights, cfg.turnover_penalty, expected_returns, cov)

        # 8. 清理小仓位
        for i in range(len(weights)):
            if weights[i] < cfg.min_weight:
                weights[i] = 0

        # 9. 归一化
        total = weights.sum()
        if total > 0:
            weights = weights / total

        # 10. 计算组合指标
        port_return = weights @ expected_returns
        port_vol = np.sqrt(weights @ cov @ weights)
        sharpe = port_return / port_vol if port_vol > 0 else 0

        # 行业暴露
        sector_exp = {}
        if sector_map:
            for i, code in enumerate(codes):
                if weights[i] > 0:
                    sector = sector_map.get(code, "其他")
                    sector_exp[sector] = sector_exp.get(sector, 0) + weights[i]

        # 换手率
        turnover = 0
        if current_weights:
            for i, code in enumerate(codes):
                old_w = current_weights.get(code, 0)
                turnover += abs(weights[i] - old_w)
            # 已清仓的
            for code, old_w in current_weights.items():
                if code not in codes:
                    turnover += abs(old_w)
            turnover /= 2  # 单边换手

        # 构建结果
        weight_dict = {}
        details = []
        for i, code in enumerate(codes):
            if weights[i] > 0:
                weight_dict[code] = round(float(weights[i]), 4)
                details.append({
                    "code": code,
                    "name": valid[i].get("name", ""),
                    "weight": round(float(weights[i]), 4),
                    "score": round(float(valid[i].get("score", 0)), 2),
                    "sector": sector_map.get(code, "") if sector_map else "",
                })

        return OptimizedPortfolio(
            weights=weight_dict,
            expected_return=round(float(port_return), 4),
            expected_vol=round(float(port_vol), 4),
            sharpe_ratio=round(float(sharpe), 2),
            sector_exposure={k: round(v, 4) for k, v in sector_exp.items()},
            turnover=round(float(turnover), 4),
            n_positions=len(weight_dict),
            details=details,
        )

    def _mean_variance_optimize(
        self,
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
        max_weight: float = 0.15,
        risk_aversion: float = 2.0,
    ) -> np.ndarray:
        """均值方差优化（封闭解 + 约束投影）

        max: w'μ - (γ/2) w'Σw
        s.t.: 0 <= w_i <= max_weight, sum(w) = 1
        """
        n = len(expected_returns)
        if n == 0:
            return np.array([])

        # 初始解：无约束最优
        try:
            inv_cov = np.linalg.pinv(cov_matrix)
            w_unc = inv_cov @ expected_returns / (risk_aversion * np.ones(n) @ inv_cov @ expected_returns + 1e-10)
        except Exception:
            w_unc = np.ones(n) / n

        # 约束投影：裁剪到 [0, max_weight]
        w = np.clip(w_unc, 0, max_weight)

        # 归一化
        total = w.sum()
        if total > 0:
            w = w / total
        else:
            # fallback：等权
            w = np.ones(n) / n

        # 迭代约束投影（最多10轮）
        for _ in range(10):
            w = np.clip(w, 0, max_weight)
            total = w.sum()
            if total > 0:
                w = w / total
            # 检查是否所有约束满足
            if np.all(w <= max_weight + 1e-6) and abs(w.sum() - 1) < 1e-6:
                break

        return w

    def _apply_sector_constraints(
        self,
        weights: np.ndarray,
        codes: List[str],
        sector_map: Dict[str, str],
        max_sector_pct: float,
    ) -> np.ndarray:
        """行业约束：超限行业按比例削减"""
        sector_weights = {}
        code_to_idx = {c: i for i, c in enumerate(codes)}

        for i, code in enumerate(codes):
            sector = sector_map.get(code, "其他")
            if sector not in sector_weights:
                sector_weights[sector] = []
            sector_weights[sector].append(i)

        for sector, indices in sector_weights.items():
            sector_total = sum(weights[i] for i in indices)
            if sector_total > max_sector_pct:
                # 按比例削减
                scale = max_sector_pct / sector_total
                for i in indices:
                    weights[i] *= scale

        # 重新归一化
        total = weights.sum()
        if total > 0:
            weights = weights / total

        return weights

    def _apply_turnover_penalty(
        self,
        weights: np.ndarray,
        codes: List[str],
        current_weights: Dict[str, float],
        penalty: float,
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
    ) -> np.ndarray:
        """换手惩罚：减少不经济的调仓"""
        old_w = np.array([current_weights.get(c, 0) for c in codes])
        trade = weights - old_w
        trade_cost = penalty * np.abs(trade).sum()

        # 计算调仓的预期收益提升
        old_return = old_w @ expected_returns
        new_return = weights @ expected_returns
        benefit = new_return - old_return

        # 如果调仓成本 > 收益提升，部分回退
        if trade_cost > benefit and benefit > 0:
            # 混合：50%新 + 50%旧
            weights = 0.5 * weights + 0.5 * old_w
            total = weights.sum()
            if total > 0:
                weights = weights / total

        return weights

    def risk_parity_weights(
        self,
        volatilities: np.ndarray,
        correlation_matrix: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """风险平价权重

        每个资产对组合风险的贡献相等
        """
        n = len(volatilities)
        if n == 0:
            return np.array([])

        # 简化版：用逆波动率加权
        inv_vol = 1.0 / np.maximum(volatilities, 1e-6)
        w = inv_vol / inv_vol.sum()
        return w
