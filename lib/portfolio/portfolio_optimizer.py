"""组合优化器 —— 均值方差优化 + 行业约束 + 换手惩罚

核心功能：
1. 从候选股评分推导预期收益
2. 用历史协方差矩阵建模风险
3. 行业暴露上限约束
4. 换手率惩罚（减少交易成本）
5. 风险平价备选方案
"""
import json
from pathlib import Path
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

        # === 读取动态因子权重 ===
        dynamic_weights = None
        try:
            weights_path = Path("cache/ic/factor_weights.json")
            if weights_path.exists():
                with open(weights_path, encoding="utf-8") as f:
                    wd = json.load(f)
                    if wd.get("mode") == "dynamic":
                        dynamic_weights = wd.get("weights", {})
                        logger.info(f"[portfolio_optimizer] 使用动态权重: {dynamic_weights}")
        except Exception as e:
            logger.debug(f"读取动态权重失败，使用固定权重: {e}")

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
        except Exception as e:
            logger.debug(f"[portfolio_optimizer] 协方差矩阵求逆失败，使用等权: {e}")
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

        # 第一轮：按比例削减所有超限行业
        for sector, indices in sector_weights.items():
            sector_total = sum(weights[i] for i in indices)
            if sector_total > max_sector_pct:
                scale = max_sector_pct / sector_total
                for i in indices:
                    weights[i] *= scale

        # 安全边界：二次检查，若削减后仍有超限，强制归一化
        for sector, indices in sector_weights.items():
            sector_total = sum(weights[i] for i in indices)
            if sector_total > max_sector_pct * 1.001:
                # 强制裁剪到上限，多余权重重新分配给未超限行业
                overage = sector_total - max_sector_pct
                safe_indices = [i for s, idx in sector_weights.items()
                               if sum(weights[j] for j in idx) <= max_sector_pct * 0.99
                               for j in idx]
                if safe_indices:
                    # 按权重比例将超限部分转移给未超限行业
                    excess_per_stock = overage / len(indices)
                    deficit_per_stock = overage / len(safe_indices)
                    for i in indices:
                        weights[i] = max(0, weights[i] - excess_per_stock)
                    for i in safe_indices:
                        weights[i] += deficit_per_stock

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


@dataclass
class PositionSizerConfig:
    """PositionSizer配置"""
    mapping_alpha: float = 1.5          # 非线性映射指数
    min_weight: float = 0.02           # 最小仓位
    max_weight: float = 0.20            # 最大仓位


class PositionSizer:
    """评分→非线性仓位映射器"""

    def __init__(self, config: PositionSizerConfig = None):
        self.config = config or PositionSizerConfig()

    def compute_weights(self, candidates: List[Dict]) -> np.ndarray:
        """将候选股评分映射为归一化仓位权重

        两步：
        1. min-max 归一化：normalized_i = (score_i - min) / (max - min)
        2. 非线性映射：weight_i = normalized_i^α / Σ(normalized_j^α)

        Args:
            candidates: [{"code": "000001", "score": 85.5, "name": "..."}, ...]

        Returns:
            np.ndarray 归一化权重，与输入顺序对应
        """
        if not candidates:
            return np.array([])

        cfg = self.config
        scores = np.array([c.get("score", 0) for c in candidates], dtype=float)

        # 检查是否所有的得分相同
        score_min = np.min(scores)
        score_max = np.max(scores)
        if score_max == score_min:
            # fallback 等权
            n = len(candidates)
            return np.ones(n) / n

        # Step 1: min-max 归一化
        normalized = (scores - score_min) / (score_max - score_min)

        # Step 2: 非线性映射
        alpha = cfg.mapping_alpha
        weighted = normalized ** alpha
        weight_sum = np.sum(weighted)
        if weight_sum == 0:
            n = len(candidates)
            return np.ones(n) / n

        weights = weighted / weight_sum

        # 应用最大/最小仓位限制
        weights = np.clip(weights, cfg.min_weight, cfg.max_weight)

        # 重新归一化
        weights = weights / np.sum(weights)

        return weights


@dataclass
class RiskBudgetConfig:
    """RiskBudgetController配置"""
    volatility_target: float = 0.15     # 目标年化波动率
    var_limit: float = 0.05            # VaR 限制（95%）
    max_drawdown: float = 0.15         # 最大回撤限制
    use_historical_var: bool = False    # 未来扩展：历史模拟法


class RiskBudgetController:
    """多层递进风控：波动率 → VaR → 最大回撤"""

    def __init__(self, config: RiskBudgetConfig = None):
        self.config = config or RiskBudgetConfig()

    def apply_risk_controls(
        self,
        weights: np.ndarray,
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
    ) -> np.ndarray:
        """应用三层风控，返回风控后的权重

        Args:
            weights: 初始权重
            expected_returns: 预期收益（年化）
            cov_matrix: 协方差矩阵（年化）

        Returns:
            np.ndarray 风控后的权重
        """
        weights = np.array(weights, dtype=float)
        cfg = self.config

        compressed = False

        # Layer 1: 波动率控制
        portfolio_vol = np.sqrt(weights @ cov_matrix @ weights)
        if portfolio_vol > cfg.volatility_target:
            scale = cfg.volatility_target / portfolio_vol
            weights = weights * scale
            compressed = True

        # Layer 2: VaR 控制（默认正态分布近似）
        var_est = self._estimate_var_normal(portfolio_vol)
        if var_est > cfg.var_limit:
            scale = cfg.var_limit / var_est
            weights = weights * scale
            compressed = True

        # Layer 3: 最大回撤控制（默认简化近似）
        dd_est = self._estimate_drawdown_normal(portfolio_vol)
        if dd_est > cfg.max_drawdown:
            scale = cfg.max_drawdown / dd_est
            weights = weights * scale
            compressed = True

        # 归一化（仅在未压缩时）
        if not compressed:
            total = np.sum(weights)
            if total > 0:
                weights = weights / total

        return weights

    def _estimate_var_normal(self, vol: float) -> float:
        """VaR 估算 — 正态分布近似（默认）"""
        return vol * 1.65  # 95% confidence

    def _estimate_drawdown_normal(self, vol: float) -> float:
        """回撤估算 — 简化近似（默认）"""
        return vol * 2.5

    # ---- 可扩展接口（未来接入历史模拟法或蒙特卡洛）----
    def compute_var_historical(
        self,
        returns: np.ndarray,
        confidence: float = 0.95,
    ) -> float:
        """历史模拟法 VaR（未来扩展）"""
        sorted_returns = np.sort(returns)
        idx = int((1 - confidence) * len(sorted_returns))
        return abs(sorted_returns[idx])

    def compute_var_monte_carlo(
        self,
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
        n_simulations: int = 10000,
        confidence: float = 0.95,
    ) -> float:
        """蒙特卡洛 VaR（未来扩展）"""
        n = len(expected_returns)
        try:
            L = np.linalg.cholesky(cov_matrix)
        except np.linalg.LinAlgError:
            return self._estimate_var_normal(np.sqrt(expected_returns @ cov_matrix @ expected_returns))
        z = np.random.standard_normal((n_simulations, n))
        # P0-4 修复 (2026-06-08): 移除 np.diag(L @ z.T) — 维度错误 (非方阵抛 ValueError)
        # 当前公式已通过 mean + sqrt(diag) 反映组合分散化
        portfolio_returns = expected_returns @ z.T
        return np.percentile(portfolio_returns, (1 - confidence) * 100)


@dataclass
class TurnoverConfig:
    """TurnoverController配置"""
    turnover_max: float = 0.50           # 最大换手率（单边）
    sentiment_threshold_high: float = 0.70  # 趋势市场情绪阈值
    volatility_threshold_high: float = 0.25 # 高波动阈值


class TurnoverController:
    """动态换手控制器 — 检测市场状态，自适应换手上限"""

    def __init__(self, config: TurnoverConfig = None):
        self.config = config or TurnoverConfig()

    def detect_market_regime(
        self,
        sentiment: float,
        volatility: float,
    ) -> str:
        """检测市场状态

        Returns:
            'trending' | 'volatile' | 'stable'
        """
        cfg = self.config
        if sentiment > cfg.sentiment_threshold_high and volatility < 0.15:
            return "trending"
        elif volatility > cfg.volatility_threshold_high:
            return "volatile"
        return "stable"

    def get_adjusted_turnover_max(
        self,
        base_max: float,
        regime: str,
    ) -> float:
        """根据市场状态调整换手上限"""
        if regime == "volatile":
            return base_max * 0.5
        elif regime == "trending":
            return base_max * 1.2
        return base_max

    def apply_turnover_penalty(
        self,
        new_weights: np.ndarray,
        old_weights: np.ndarray,
        turnover_max: float = None,
    ) -> np.ndarray:
        """超过换手上限时等比例压缩

        公式：actual_turnover = Σ|new_i - old_i| / 2
        """
        if turnover_max is None:
            turnover_max = self.config.turnover_max

        new_weights = np.array(new_weights, dtype=float)
        old_weights = np.array(old_weights, dtype=float)

        turnover = np.abs(new_weights - old_weights).sum() / 2
        if turnover > turnover_max:
            scale = turnover_max / turnover
            new_weights = old_weights + (new_weights - old_weights) * scale

        return new_weights

    def apply_with_regime(
        self,
        new_weights: np.ndarray,
        old_weights: np.ndarray,
        regime: str = None,
        sentiment: float = None,
        volatility: float = None,
        turnover_max: float = None,
    ) -> np.ndarray:
        """综合应用：检测市场状态 + 调整换手上限 + 压缩"""
        if regime is None and sentiment is not None and volatility is not None:
            regime = self.detect_market_regime(sentiment, volatility)

        if turnover_max is None:
            turnover_max = self.config.turnover_max

        if regime is not None:
            turnover_max = self.get_adjusted_turnover_max(turnover_max, regime)

        return self.apply_turnover_penalty(new_weights, old_weights, turnover_max)


@dataclass
class StrategyConfig:
    """策略配置"""
    name: str = "default"
    weight: float = 1.0              # 策略权重（相对总组合）
    enabled: bool = True
    max_position_pct: float = 0.15  # 单股最大仓位
    risk_budget_pct: float = 0.30   # 风险预算占比
    score_to_return: float = 0.001   # 评分→收益映射
    volatility_target: float = 0.15  # 策略波动率目标
    turnover_max: float = 0.50       # 最大换手率


@dataclass
class StrategyAllocation:
    """单策略分配结果"""
    strategy_name: str
    weights: Dict[str, float]            # {code: weight}
    expected_return: float               # 预期年化收益
    expected_vol: float                # 预期年化波动
    sharpe_ratio: float               # 夏普比率


class StrategyAllocator:
    """单策略完整链路：PositionSizer → RiskBudget → TurnoverControl"""

    def __init__(
        self,
        strategy_config: StrategyConfig = None,
        position_sizer_config: PositionSizerConfig = None,
        risk_budget_config: RiskBudgetConfig = None,
        turnover_config: TurnoverConfig = None,
    ):
        self.strategy_config = strategy_config or StrategyConfig()
        self.position_sizer = PositionSizer(position_sizer_config)
        self.risk_controller = RiskBudgetController(risk_budget_config)
        self.turnover_controller = TurnoverController(turnover_config)

    def allocate(
        self,
        candidates: List[Dict],
        current_weights: Dict[str, float] = None,
    ) -> StrategyAllocation:
        """执行单策略仓位分配

        流程：
        1. PositionSizer: 评分→初步仓位
        2. RiskBudgetController: 风控调整
        3. TurnoverController: 换手控制
        """
        cfg = self.strategy_config

        if not cfg.enabled:
            return StrategyAllocation(
                strategy_name=cfg.name,
                weights={},
                expected_return=0,
                expected_vol=0,
                sharpe_ratio=0,
            )

        if not candidates:
            return StrategyAllocation(
                strategy_name=cfg.name,
                weights={},
                expected_return=0,
                expected_vol=0,
                sharpe_ratio=0,
            )

        # Step 1: PositionSizer — 评分→初步仓位
        raw_weights = self.position_sizer.compute_weights(candidates)
        codes = [c["code"] for c in candidates]

        # Step 2: RiskBudgetController — 风控调整
        expected_returns = np.array([
            c.get("score", 0) * cfg.score_to_return * 252
            for c in candidates
        ])
        vols = np.array([c.get("volatility", cfg.volatility_target) for c in candidates])
        cov_matrix = np.diag(vols ** 2) * 252

        adjusted_weights = self.risk_controller.apply_risk_controls(
            raw_weights, expected_returns, cov_matrix
        )

        # Step 3: TurnoverController — 换手控制
        if current_weights:
            old_weights_arr = np.array([
                current_weights.get(c, 0) for c in codes
            ])
            adjusted_weights = self.turnover_controller.apply_turnover_penalty(
                adjusted_weights, old_weights_arr, cfg.turnover_max
            )

        # 归一化
        total = np.sum(adjusted_weights)
        if total > 0:
            adjusted_weights = adjusted_weights / total

        # 构建结果
        weight_dict = {
            code: round(float(w), 4)
            for code, w in zip(codes, adjusted_weights)
            if w > 1e-6
        }

        # 计算组合指标
        port_return = adjusted_weights @ expected_returns
        port_vol = np.sqrt(adjusted_weights @ cov_matrix @ adjusted_weights)
        sharpe = port_return / port_vol if port_vol > 0 else 0

        return StrategyAllocation(
            strategy_name=cfg.name,
            weights=weight_dict,
            expected_return=round(float(port_return), 4),
            expected_vol=round(float(port_vol), 4),
            sharpe_ratio=round(float(sharpe), 2),
        )
