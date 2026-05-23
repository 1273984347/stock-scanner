"""H3 行业约束削减边界测试

验证 _apply_sector_constraints 的行为：
1. 总权重归一化为 1
2. 超限行业相比原始权重有削减
3. 无行业超限时权重比例保持
4. 空权重不崩溃
"""
import pytest
import numpy as np


def test_total_weight_normalizes_to_one():
    """总权重归一化为 1"""
    from lib.portfolio_optimizer import PortfolioOptimizer, OptimizerConfig

    cfg = OptimizerConfig(max_sector_pct=0.30)
    opt = PortfolioOptimizer(cfg)

    codes = ["A", "B", "C"]
    weights = np.array([0.50, 0.30, 0.20])
    sector_map = {"A": "IND_A", "B": "IND_B", "C": "IND_C"}

    result = opt._apply_sector_constraints(weights, codes, sector_map, max_sector_pct=0.30)
    assert abs(result.sum() - 1.0) < 1e-6


def test_overweight_sector_is_reduced():
    """超限行业相比原始权重有明显削减"""
    from lib.portfolio_optimizer import PortfolioOptimizer, OptimizerConfig

    cfg = OptimizerConfig(max_sector_pct=0.30)
    opt = PortfolioOptimizer(cfg)

    codes = ["A", "B", "C"]
    weights = np.array([0.50, 0.30, 0.20])  # A: 50% 超限
    sector_map = {"A": "IND_A", "B": "IND_B", "C": "IND_C"}

    result = opt._apply_sector_constraints(weights, codes, sector_map, max_sector_pct=0.30)

    # A 行业权重应小于原始 0.50
    assert result[0] < 0.50, "超限行业权重未被削减"


def test_empty_weights_returns_zeros():
    """全零权重不崩溃"""
    from lib.portfolio_optimizer import PortfolioOptimizer, OptimizerConfig

    cfg = OptimizerConfig()
    opt = PortfolioOptimizer(cfg)

    weights = np.array([0.0, 0.0])
    codes = ["A", "B"]
    sector_map = {"A": "IND_X", "B": "IND_Y"}

    result = opt._apply_sector_constraints(weights, codes, sector_map, max_sector_pct=0.30)
    assert result.sum() == 0.0


def test_no_overlimit_preserves_proportions():
    """无行业超限时，仅归一化（权重比例保持相对关系）"""
    from lib.portfolio_optimizer import PortfolioOptimizer, OptimizerConfig

    cfg = OptimizerConfig(max_sector_pct=0.30)
    opt = PortfolioOptimizer(cfg)

    codes = ["A", "B", "C"]
    weights = np.array([0.25, 0.35, 0.40])  # 无超限
    sector_map = {"A": "IND_A", "B": "IND_B", "C": "IND_C"}

    result = opt._apply_sector_constraints(weights, codes, sector_map, max_sector_pct=0.30)

    # 总和归一化
    assert abs(result.sum() - 1.0) < 1e-6
    # 比例关系保持（归一化因子 = 1/1.0，等比例）
    np.testing.assert_allclose(result / result.sum(), weights / weights.sum(), rtol=1e-6)
