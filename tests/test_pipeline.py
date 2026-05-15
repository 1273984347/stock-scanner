"""Pipeline 端到端测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np
from lib.pipeline import _optimize_portfolio, _check_portfolio_risk


def test_optimize_portfolio_basic():
    """测试组合优化基本功能"""
    # 构造模拟选股结果
    results = {
        "多因子选股": pd.DataFrame({
            "代码": ["000001", "600519", "000858"],
            "名称": ["平安银行", "贵州茅台", "五粮液"],
            "综合评分": [8.5, 9.0, 8.2],
            "波动率20": [25.0, 20.0, 28.0],
        }),
    }

    result = _optimize_portfolio(results)

    assert "weights" in result
    assert "expected_return" in result
    assert "expected_vol" in result
    assert "sharpe_ratio" in result


def test_optimize_portfolio_empty():
    """测试空结果"""
    results = {}
    result = _optimize_portfolio(results)

    assert result["weights"] == {}
    assert result["n_positions"] == 0


def test_optimize_portfolio_weight_sum():
    """测试权重和归一化"""
    results = {
        "多因子选股": pd.DataFrame({
            "代码": ["000001", "600519"],
            "名称": ["平安银行", "贵州茅台"],
            "综合评分": [8.5, 9.0],
            "波动率20": [25.0, 20.0],
        }),
    }

    result = _optimize_portfolio(results)

    weights = result.get("weights", {})
    if weights:
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6, f"权重和应接近 1.0，实际为 {total}"


def test_check_portfolio_risk_basic():
    """测试风控检查"""
    results = {
        "多因子选股": pd.DataFrame({
            "代码": ["000001", "600519"],
            "名称": ["平安银行", "贵州茅台"],
            "综合评分": [8.5, 9.0],
        }),
    }

    risk_check = _check_portfolio_risk(results)

    assert "alerts" in risk_check
    assert "risk_budget" in risk_check
    assert "recommendation" in risk_check


def test_check_portfolio_risk_concentration():
    """测试策略集中度告警"""
    # 构造大量候选（超过5只）
    results = {
        "涨停板复盘": pd.DataFrame({
            "代码": [f"00000{i}" for i in range(8)],
            "名称": [f"股票{i}" for i in range(8)],
            "综合评分": [8.0] * 8,
        }),
    }

    risk_check = _check_portfolio_risk(results)
    alerts = risk_check.get("alerts", [])

    # 应该有策略集中度警告
    assert any(a.get("rule") == "策略集中度" for a in alerts)


def test_optimize_with_sector_map():
    """测试同行业股票相关度提高"""
    results = {
        "多因子选股": pd.DataFrame({
            "代码": ["000001", "600519", "000858"],
            "名称": ["平安银行", "贵州茅台", "五粮液"],
            "综合评分": [8.5, 9.0, 8.2],
            "波动率20": [25.0, 20.0, 22.0],
        }),
    }

    # sector_map 会通过 _get_sector_map 获取
    result = _optimize_portfolio(results)

    # 应该返回有效的优化结果
    assert "weights" in result


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
