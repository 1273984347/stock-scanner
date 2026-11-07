"""DCF 估值模型测试"""
import pytest
import numpy as np
from lib.scoring.dcf import dcf_valuation, sensitivity_matrix, margin_of_safety


class TestDCFValuation:
    """dcf_valuation 函数测试"""

    def test_basic_positive_fcf(self):
        """基本 DCF 计算 - 正向现金流"""
        result = dcf_valuation(fcf=100.0, growth=0.10, discount=0.15)
        assert "error" not in result
        assert "per_share_value" in result
        assert result["per_share_value"] > 0
        assert "forecast_fcfs" in result
        assert len(result["forecast_fcfs"]) == 5

    def test_zero_fcf_returns_error(self):
        """FCF 为零应返回错误"""
        result = dcf_valuation(fcf=0, growth=0.15, discount=0.10)
        assert "error" in result
        assert "FCF 必须为正数" in result["error"]

    def test_negative_fcf_returns_error(self):
        """负 FCF 应返回错误"""
        result = dcf_valuation(fcf=-50.0, growth=0.15, discount=0.10)
        assert "error" in result
        assert "FCF 必须为正数" in result["error"]

    def test_growth_equals_discount_returns_error(self):
        """增长率等于折现率应报错"""
        result = dcf_valuation(fcf=100.0, growth=0.10, discount=0.10)
        assert "error" in result
        assert "增长率不应大于等于折现率" in result["error"]

    def test_growth_exceeds_discount_returns_error(self):
        """增长率大于折现率应报错"""
        result = dcf_valuation(fcf=100.0, growth=0.15, discount=0.10)
        assert "error" in result

    def test_terminal_growth_equals_discount_returns_error(self):
        """永续增长率等于折现率应报错"""
        result = dcf_valuation(fcf=100.0, growth=0.05, discount=0.10, terminal_growth=0.10)
        assert "error" in result
        assert "永续增长率必须小于折现率" in result["error"]

    def test_custom_years(self):
        """自定义预测期"""
        result = dcf_valuation(fcf=100.0, growth=0.10, discount=0.15, years=10)
        assert "error" not in result
        assert len(result["forecast_fcfs"]) == 10

    def test_with_debt_and_cash(self):
        """含负债和现金的估值"""
        result = dcf_valuation(fcf=100.0, growth=0.10, discount=0.15,
                               debt=50.0, cash=20.0, shares=10.0)
        assert "error" not in result
        # 股权价值 = 企业价值 - 负债 + 现金
        assert result["equity_value"] == result["enterprise_value"] - 50.0 + 20.0
        # 每股价值 = 股权价值 / 股本
        assert result["per_share_value"] == pytest.approx(result["equity_value"] / 10.0, rel=1e-2)

    def test_zero_shares_returns_zero_per_share(self):
        """零股本应返回零每股价值"""
        result = dcf_valuation(fcf=100.0, growth=0.10, discount=0.15, shares=0)
        assert "error" not in result
        assert result["per_share_value"] == 0

    def test_forecast_fcfs_grow(self):
        """预测现金流应随时间增长"""
        result = dcf_valuation(fcf=100.0, growth=0.10, discount=0.15)
        fcfs = result["forecast_fcfs"]
        for i in range(1, len(fcfs)):
            assert fcfs[i] > fcfs[i - 1], "现金流应逐年增长"


class TestSensitivityMatrix:
    """敏感性分析矩阵测试"""

    def test_matrix_shape(self):
        """矩阵维度应匹配输入范围"""
        growth_range = [0.05, 0.10, 0.15]
        discount_range = [0.08, 0.10, 0.12]
        result = sensitivity_matrix(fcf=100.0, growth_range=growth_range,
                                     discount_range=discount_range)
        assert len(result["matrix"]) == len(growth_range)
        for row in result["matrix"]:
            assert len(row) == len(discount_range)

    def test_invalid_combinations_are_none(self):
        """无效组合（增长率 >= 折现率）应为 None"""
        growth_range = [0.05, 0.10, 0.15]
        discount_range = [0.08, 0.09, 0.10]
        result = sensitivity_matrix(fcf=100.0, growth_range=growth_range,
                                     discount_range=discount_range)
        # g=0.10, r=0.10 -> None; g=0.15, r=0.10 -> None
        assert result["matrix"][1][1] is None
        assert result["matrix"][2][1] is None

    def test_valid_combinations_have_values(self):
        """有效组合应有数值"""
        growth_range = [0.05, 0.10]
        discount_range = [0.08, 0.12]
        result = sensitivity_matrix(fcf=100.0, growth_range=growth_range,
                                     discount_range=discount_range)
        assert result["matrix"][0][0] is not None
        assert result["matrix"][0][0] > 0

    def test_ranges_returned(self):
        """范围参数应被返回"""
        growth_range = [0.05, 0.10]
        discount_range = [0.08, 0.12]
        result = sensitivity_matrix(fcf=100.0, growth_range=growth_range,
                                     discount_range=discount_range)
        assert result["growth_range"] == growth_range
        assert result["discount_range"] == discount_range


class TestMarginOfSafety:
    """安全边际测试"""

    def test_undervalued_stock(self):
        """低估股票"""
        result = margin_of_safety(intrinsic_value=100.0, market_price=70.0)
        assert "error" not in result
        assert result["margin_of_safety"] > 0
        assert result["upside"] > 0
        assert result["verdict"] == "低估"

    def test_fairly_valued_stock(self):
        """合理估值"""
        result = margin_of_safety(intrinsic_value=100.0, market_price=85.0)
        assert result["verdict"] == "合理"
        assert 0 <= result["margin_of_safety"] <= 0.25

    def test_overvalued_stock(self):
        """高估股票"""
        result = margin_of_safety(intrinsic_value=100.0, market_price=120.0)
        assert result["margin_of_safety"] < 0
        assert result["upside"] < 0
        assert result["verdict"] == "高估"

    def test_zero_intrinsic_value_returns_error(self):
        """内在价值为零应报错"""
        result = margin_of_safety(intrinsic_value=0.0, market_price=100.0)
        assert "error" in result
        assert "内在价值必须为正" in result["error"]

    def test_negative_intrinsic_value_returns_error(self):
        """负内在价值应报错"""
        result = margin_of_safety(intrinsic_value=-50.0, market_price=100.0)
        assert "error" in result

    def test_zero_market_price(self):
        """零市价应正常返回（upside 无穷大但代码处理了）"""
        result = margin_of_safety(intrinsic_value=100.0, market_price=0.0)
        assert "error" not in result
        assert result["upside"] == 0  # 代码中除以零保护
