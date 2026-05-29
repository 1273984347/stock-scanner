"""仓位计算模块测试

测试覆盖：
- RiskSizedPosition 基础仓位计算
- 风险预算 + 止损距离 → 股数
- 现金限制 / 单票上限 / 整手取整
- ATR 止损计算
- 批量仓位计算
- Kelly 公式
- 波动率目标仓位
- atr_risk_size 函数
- discrete_allocation 函数
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd

from lib.position_size import (
    PositionSizeConfig,
    PositionSizeResult,
    RiskSizedPosition,
    KellyPosition,
    VolTargetPosition,
    atr_risk_size,
    discrete_allocation,
)


@pytest.fixture
def sizer():
    return RiskSizedPosition()


@pytest.fixture
def conservative_sizer():
    return RiskSizedPosition(PositionSizeConfig(
        risk_per_trade_pct=0.3,
        max_position_pct=0.10,
        round_lot=100,
    ))


# ========== RiskSizedPosition 基础计算 ==========

class TestRiskSizedPosition:
    def test_basic_calculation(self, sizer):
        result = sizer.calculate(
            equity=1_000_000,
            entry_price=10.0,
            stop_price=9.5,  # 止损距离 0.5
        )
        # risk_budget = 1M * 0.5% = 5000
        # risk_qty = 5000 / 0.5 = 10000
        assert result.qty > 0
        assert result.risk_budget == pytest.approx(5000)
        assert result.stop_distance == pytest.approx(0.5)
        assert result.notional > 0

    def test_qty_is_round_lot(self, sizer):
        result = sizer.calculate(
            equity=1_000_000,
            entry_price=10.0,
            stop_price=9.0,
        )
        assert result.qty % 100 == 0  # A股整手

    def test_weight_within_max_position(self, sizer):
        result = sizer.calculate(
            equity=1_000_000,
            entry_price=10.0,
            stop_price=9.99,  # 很小的止损距离 → 很大的风险股数
        )
        assert result.weight_pct <= sizer.config.max_position_pct * 100 + 0.01

    def test_zero_equity_returns_zero(self, sizer):
        result = sizer.calculate(equity=0, entry_price=10.0)
        assert result.qty == 0

    def test_capped_by_max_position(self, sizer):
        # 小止损 → 大仓位 → 触及单票上限
        result = sizer.calculate(
            equity=1_000_000,
            entry_price=10.0,
            stop_price=9.99,
        )
        if result.capped_by_max_position:
            assert result.weight_pct == pytest.approx(
                sizer.config.max_position_pct * 100, abs=1
            )

    def test_capped_by_cash(self, sizer):
        result = sizer.calculate(
            equity=1_000_000,
            entry_price=10.0,
            stop_price=9.0,
            cash_available=5000,  # 只有5000可用
        )
        assert result.capped_by_cash
        assert result.notional <= 5000

    def test_atr_stop_used_when_no_stop(self, sizer):
        result = sizer.calculate(
            equity=1_000_000,
            entry_price=10.0,
            atr=1.0,
        )
        # stop_price = 10 - 1.0 * 2.0 = 8.0
        assert result.stop_price == pytest.approx(8.0)

    def test_default_stop_5pct(self, sizer):
        result = sizer.calculate(
            equity=1_000_000,
            entry_price=10.0,
        )
        # 没有 ATR 也没有 stop_price → 默认 5% 止损
        assert result.stop_price == pytest.approx(9.5)


# ========== 批量计算 ==========

class TestBatchCalculation:
    def test_batch_returns_results(self, sizer):
        signals = [
            {"code": "000001", "score": 80},
            {"code": "600519", "score": 70},
        ]
        prices = {"000001": 10.0, "600519": 1800.0}
        results = sizer.calculate_batch(equity=1_000_000, signals=signals, prices=prices)
        assert "000001" in results
        assert "600519" in results

    def test_batch_respects_total_exposure(self, sizer):
        signals = [{"code": f"00000{i}", "score": 90 - i} for i in range(10)]
        prices = {f"00000{i}": 10.0 for i in range(10)}
        results = sizer.calculate_batch(equity=1_000_000, signals=signals, prices=prices)
        total_weight = sum(r.weight_pct for r in results.values())
        assert total_weight <= sizer.config.max_total_exposure * 100 + 1

    def test_batch_skips_zero_price(self, sizer):
        signals = [{"code": "000001", "score": 80}, {"code": "000002", "score": 70}]
        prices = {"000001": 10.0, "000002": 0}
        results = sizer.calculate_batch(equity=1_000_000, signals=signals, prices=prices)
        assert "000001" in results
        assert "000002" not in results

    def test_batch_sorted_by_score(self, sizer):
        signals = [
            {"code": "000001", "score": 50},
            {"code": "000002", "score": 90},
        ]
        prices = {"000001": 10.0, "000002": 10.0}
        results = sizer.calculate_batch(equity=1_000_000, signals=signals, prices=prices)
        # 高分的应该先分配，获得更大仓位
        assert results["000002"].weight_pct >= results["000001"].weight_pct


# ========== ATR 计算 ==========

class TestATR:
    def test_atr_basic(self, sizer):
        dates = pd.date_range("2026-01-01", periods=20, freq="D")
        df = pd.DataFrame({
            "high": np.full(20, 11.0),
            "low": np.full(20, 9.0),
            "close": np.full(20, 10.0),
        }, index=dates)
        atr = sizer.calculate_atr(df, period=14)
        assert atr is not None
        assert atr > 0

    def test_atr_returns_none_for_empty(self, sizer):
        assert sizer.calculate_atr(pd.DataFrame()) is None
        assert sizer.calculate_atr(None) is None

    def test_atr_returns_none_for_short_data(self, sizer):
        df = pd.DataFrame({"high": [10], "low": [9], "close": [9.5]})
        assert sizer.calculate_atr(df, period=14) is None

    def test_atr_batch(self, sizer):
        dates = pd.date_range("2026-01-01", periods=20, freq="D")
        df = pd.DataFrame({
            "high": np.full(20, 11.0),
            "low": np.full(20, 9.0),
            "close": np.full(20, 10.0),
        }, index=dates)
        atrs = sizer.calculate_atr_batch({"000001": df, "000002": df})
        assert "000001" in atrs
        assert "000002" in atrs


# ========== Kelly 公式 ==========

class TestKelly:
    def test_kelly_basic(self):
        f = KellyPosition.kelly_fraction(win_rate=0.6, avg_win=2.0, avg_loss=1.0)
        assert 0 < f <= 0.25

    def test_kelly_capped_at_25pct(self):
        f = KellyPosition.kelly_fraction(win_rate=0.9, avg_win=10.0, avg_loss=1.0)
        assert f <= 0.25

    def test_kelly_zero_on_bad_edge(self):
        f = KellyPosition.kelly_fraction(win_rate=0.3, avg_win=1.0, avg_loss=1.0)
        assert f == 0

    def test_half_kelly_is_half(self):
        full = KellyPosition.kelly_fraction(0.6, 2.0, 1.0)
        half = KellyPosition.half_kelly(0.6, 2.0, 1.0)
        assert half == pytest.approx(full / 2)

    def test_optimal_f_from_trades(self):
        trades = [0.05, -0.02, 0.03, -0.01, 0.04, -0.03, 0.06, -0.02]
        f = KellyPosition.optimal_f(trades)
        assert 0 <= f <= 0.25

    def test_optimal_f_empty_trades(self):
        assert KellyPosition.optimal_f([]) == 0.01


# ========== 波动率目标仓位 ==========

class TestVolTarget:
    def test_basic_vol_target(self):
        ratio = VolTargetPosition.calculate(target_vol=0.15, current_vol=0.30)
        assert ratio == pytest.approx(0.5)

    def test_capped_at_max_leverage(self):
        ratio = VolTargetPosition.calculate(target_vol=0.30, current_vol=0.10, max_leverage=1.0)
        assert ratio == 1.0

    def test_zero_current_vol(self):
        ratio = VolTargetPosition.calculate(target_vol=0.15, current_vol=0)
        assert ratio == 1.0  # 返回 max_leverage

    def test_from_returns(self):
        np.random.seed(42)
        returns = np.random.randn(100) * 0.02
        ratio = VolTargetPosition.calculate_from_returns(returns, target_vol=0.15)
        assert 0 < ratio <= 1.0

    def test_from_returns_short_data(self):
        returns = np.array([0.01, -0.01, 0.02])
        ratio = VolTargetPosition.calculate_from_returns(returns)
        assert ratio == 0.5  # 数据不足返回默认值


# ========== atr_risk_size 函数 ==========

class TestAtrRiskSize:
    def test_basic_atr_risk(self):
        # cash=100000, price=10, atr=0.5, risk_ratio=0.01, multiplier=2
        # risk_budget=1000, risk_per_share=1.0, shares=1000 → 10手
        shares = atr_risk_size(100000, 10.0, 0.5)
        assert shares > 0
        assert shares % 100 == 0

    def test_zero_inputs_returns_zero(self):
        assert atr_risk_size(0, 10.0, 0.5) == 0
        assert atr_risk_size(100000, 0, 0.5) == 0
        assert atr_risk_size(100000, 10.0, 0) == 0

    def test_small_budget_returns_zero(self):
        # 风险预算太小，不足一手
        shares = atr_risk_size(1000, 10.0, 5.0, risk_ratio=0.01)
        assert shares == 0


# ========== discrete_allocation 函数 ==========

class TestDiscreteAllocation:
    def test_basic_allocation(self):
        weights = {"000001": 0.3, "600519": 0.7}
        prices = {"000001": 10.0, "600519": 1800.0}
        alloc = discrete_allocation(weights, prices, total_value=2000000)
        assert "000001" in alloc
        assert "600519" in alloc
        # 所有分配应为整手
        for shares in alloc.values():
            assert shares % 100 == 0

    def test_empty_inputs(self):
        assert discrete_allocation({}, {}, 100000) == {}
        assert discrete_allocation({"000001": 0.5}, {"000001": 10.0}, 0) == {}

    def test_respects_total_value(self):
        weights = {"000001": 1.0}
        prices = {"000001": 10.0}
        alloc = discrete_allocation(weights, prices, total_value=5000)
        total_cost = alloc["000001"] * 10.0
        assert total_cost <= 5000

    def test_zero_weight_skipped(self):
        weights = {"000001": 0.5, "000002": 0}
        prices = {"000001": 10.0, "000002": 10.0}
        alloc = discrete_allocation(weights, prices, total_value=100000)
        assert "000002" not in alloc


# ========== 配置覆盖 ==========

class TestConfig:
    def test_custom_config(self):
        cfg = PositionSizeConfig(risk_per_trade_pct=1.0, max_position_pct=0.30, round_lot=100)
        sizer = RiskSizedPosition(cfg)
        result = sizer.calculate(equity=1_000_000, entry_price=10.0, stop_price=9.0)
        # risk_budget = 1M * 1% = 10000
        assert result.risk_budget == pytest.approx(10000)

    def test_min_position_filter(self):
        cfg = PositionSizeConfig(min_position_pct=0.05)  # 最小 5%
        sizer = RiskSizedPosition(cfg)
        # 小仓位应该被过滤为 0
        result = sizer.calculate(
            equity=1_000_000,
            entry_price=100.0,
            stop_price=99.99,  # 极小止损 → 大风险股数但可能被 max_position 限制
        )
        # 如果 weight < min_position_pct，qty 应为 0
        if 0 < result.weight_pct < 5:
            assert result.qty == 0
