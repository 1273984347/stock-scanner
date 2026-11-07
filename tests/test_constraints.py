"""约束引擎测试

测试覆盖：
- ST/*ST 股票过滤（硬约束）
- 停牌检测（硬约束）
- 极端 PE 预警（软约束）
- 净资产为负预警（软约束）
- 低流动性预警（软约束）
- 股权质押预警（软约束）
- 批量约束检查
- 自定义约束注册
- blocked/passed 分离
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pandas as pd
import numpy as np

from lib.risk.selection_constraints import (
    DeterministicConstraints,
    Constraint,
    ConstraintLevel,
    ConstraintResult,
)


@pytest.fixture
def engine():
    return DeterministicConstraints()


@pytest.fixture
def normal_market_data():
    """正常交易数据（非停牌，60+ 行满足新股检测阈值）"""
    dates = pd.date_range("2026-01-01", periods=80, freq="D")
    return pd.DataFrame({
        "date": dates,
        "close": np.linspace(10, 12, 80),
        "volume": np.full(80, 1e6),
        "amount": np.full(80, 1e7),
        "high": np.linspace(10.5, 12.5, 80),
        "low": np.linspace(9.5, 11.5, 80),
    })


@pytest.fixture
def suspended_market_data():
    """停牌数据（成交量为 0）"""
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    return pd.DataFrame({
        "date": dates,
        "close": np.full(10, 10.0),
        "volume": np.zeros(10),
        "amount": np.zeros(10),
    })


@pytest.fixture
def low_liquidity_data():
    """低流动性数据"""
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    return pd.DataFrame({
        "date": dates,
        "close": np.full(25, 10.0),
        "volume": np.full(25, 1000),
        "amount": np.full(25, 5e5),  # 50万，低于1000万阈值
    })


# ========== ST 过滤 ==========

class TestSTFilter:
    def test_st_stock_blocked(self, engine):
        result = engine.check_ticker("000001", stock_name="ST某某")
        assert result.blocked
        assert not result.passed
        assert any(v["constraint"] == "st_delist" for v in result.violations)

    def test_star_st_stock_blocked(self, engine):
        result = engine.check_ticker("000002", stock_name="*ST某某")
        assert result.blocked
        assert any(v["constraint"] == "st_delist" for v in result.violations)

    def test_normal_stock_not_blocked(self, engine):
        result = engine.check_ticker("000001", stock_name="平安银行")
        assert not result.blocked
        assert result.passed

    def test_delist_risk_blocked(self, engine):
        result = engine.check_ticker("000003", stock_name="某某退市")
        assert result.blocked

    def test_financial_st_flag(self, engine):
        fin = pd.Series({"is_st": True})
        result = engine.check_ticker("000004", financial_data=fin)
        assert result.blocked


# ========== 停牌检测 ==========

class TestSuspension:
    def test_zero_volume_detected(self, engine, suspended_market_data):
        result = engine.check_ticker("000001", market_data=suspended_market_data)
        assert result.blocked
        assert any(v["constraint"] == "suspension" for v in result.violations)

    def test_normal_volume_not_blocked(self, engine, normal_market_data):
        result = engine.check_ticker("000001", market_data=normal_market_data)
        assert not any(v["constraint"] == "suspension" for v in result.violations)

    def test_no_market_data_not_blocked(self, engine):
        result = engine.check_ticker("000001")
        assert not any(v["constraint"] == "suspension" for v in result.violations)


# ========== 软约束 ==========

class TestSoftConstraints:
    def test_extreme_pe_penalty(self, engine):
        fin = pd.Series({"pe": 300})
        result = engine.check_ticker("000001", financial_data=fin)
        assert not result.blocked  # 软约束不 block
        assert result.score_adjustment < 0
        assert any(v["constraint"] == "extreme_pe" for v in result.violations)

    def test_negative_pe_penalty(self, engine):
        fin = pd.Series({"pe": -10})
        result = engine.check_ticker("000001", financial_data=fin)
        assert result.score_adjustment < 0

    def test_normal_pe_no_penalty(self, engine):
        fin = pd.Series({"pe": 15})
        result = engine.check_ticker("000001", financial_data=fin)
        assert not any(v["constraint"] == "extreme_pe" for v in result.violations)

    def test_negative_equity_penalty(self, engine):
        fin = pd.Series({"net_assets": -1e8})
        result = engine.check_ticker("000001", financial_data=fin)
        assert result.score_adjustment < 0
        assert any(v["constraint"] == "negative_equity" for v in result.violations)

    def test_low_liquidity_penalty(self, engine, low_liquidity_data):
        result = engine.check_ticker("000001", market_data=low_liquidity_data)
        assert result.score_adjustment < 0
        assert any(v["constraint"] == "low_liquidity" for v in result.violations)

    def test_high_pledge_penalty(self, engine):
        fin = pd.Series({"pledge_ratio": 0.70})
        result = engine.check_ticker("000001", financial_data=fin)
        assert result.score_adjustment < 0
        assert any(v["constraint"] == "high_pledge_ratio" for v in result.violations)

    def test_negative_growth_penalty(self, engine):
        fin = pd.Series({"revenue_growth": -0.1, "prev_revenue_growth": -0.05})
        result = engine.check_ticker("000001", financial_data=fin)
        assert result.score_adjustment < 0
        assert any(v["constraint"] == "negative_growth" for v in result.violations)

    def test_small_cap_penalty(self, engine):
        fin = pd.Series({"market_cap": 1e9})  # 10亿
        result = engine.check_ticker("000001", financial_data=fin)
        assert result.score_adjustment < 0
        assert any(v["constraint"] == "small_market_cap" for v in result.violations)


# ========== 新股检测 ==========

class TestNewStock:
    def test_short_history_detected(self, engine):
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        df = pd.DataFrame({"close": range(30)}, index=dates)
        result = engine.check_ticker("000001", market_data=df)
        assert any(v["constraint"] == "new_stock" for v in result.violations)

    def test_sufficient_history_no_violation(self, engine, normal_market_data):
        result = engine.check_ticker("000001", market_data=normal_market_data)
        assert not any(v["constraint"] == "new_stock" for v in result.violations)


# ========== 批量检查 ==========

class TestBatchCheck:
    def test_batch_returns_results_for_all(self, engine):
        tickers = ["000001", "000002", "000003"]
        name_map = {"000001": "平安银行", "000002": "*ST某某", "000003": "万科A"}
        results = engine.check_batch(tickers, stock_name_map=name_map)
        assert len(results) == 3
        assert "000001" in results
        assert "000002" in results
        assert "000003" in results

    def test_batch_stocks_correctly_classified(self, engine):
        tickers = ["000001", "000002"]
        name_map = {"000001": "平安银行", "000002": "ST某某"}
        results = engine.check_batch(tickers, stock_name_map=name_map)
        blocked, passed = engine.filter_blocked(results)
        assert "000002" in blocked
        assert "000001" in passed

    def test_score_adjustments_aggregation(self, engine):
        tickers = ["000001", "000002"]
        fin_map = {"000001": pd.Series({"pe": 300})}
        results = engine.check_batch(tickers, financial_data_map=fin_map)
        adjustments = engine.get_score_adjustments(results)
        assert adjustments["000001"] < 0
        assert adjustments["000002"] == 0


# ========== 自定义约束 ==========

class TestCustomConstraint:
    def test_add_custom_constraint(self, engine):
        initial_count = len(engine.constraints)
        engine.add_constraint(Constraint(
            name="custom_test",
            description="测试约束",
            level=ConstraintLevel.HARD,
            check_fn=lambda ctx: "blocked" if ctx["ticker"] == "999999" else None,
        ))
        assert len(engine.constraints) == initial_count + 1

    def test_custom_hard_constraint_blocks(self, engine):
        engine.add_constraint(Constraint(
            name="block_999999",
            description="阻止 999999",
            level=ConstraintLevel.HARD,
            check_fn=lambda ctx: "blocked" if ctx["ticker"] == "999999" else None,
        ))
        result = engine.check_ticker("999999")
        assert result.blocked

    def test_custom_soft_constraint_penalizes(self, engine):
        engine.add_constraint(Constraint(
            name="soft_penalty",
            description="软惩罚",
            level=ConstraintLevel.SOFT,
            check_fn=lambda ctx: "warn",
            penalty=0.5,
        ))
        result = engine.check_ticker("000001")
        assert not result.blocked
        assert result.score_adjustment < 0


# ========== 边界条件 ==========

class TestEdgeCases:
    def test_empty_ticker_blocked(self, engine):
        result = engine.check_ticker("")
        assert result.blocked

    def test_none_ticker_blocked(self, engine):
        result = engine.check_ticker(None)
        assert result.blocked

    def test_no_data_no_violations(self, engine):
        result = engine.check_ticker("000001")
        assert result.passed
        assert len(result.violations) == 0
        assert result.score_adjustment == 0

    def test_multiple_violations_accumulate(self, engine):
        fin = pd.Series({"pe": 300, "net_assets": -1e8, "pledge_ratio": 0.80})
        result = engine.check_ticker("000001", financial_data=fin)
        assert len(result.violations) >= 3
        assert result.score_adjustment < -0.5  # 多个惩罚累加
