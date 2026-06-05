"""组合归因分析测试"""
import pytest
from lib.attribution import PortfolioAttribution, AttributionResult


class TestPortfolioAttribution:
    def _make(self):
        return PortfolioAttribution()

    def test_basic_attribution(self):
        """基本归因"""
        attr = self._make()
        portfolio = {
            "total_return": 0.10,
            "positions": [
                {"code": "600519", "sector": "白酒", "weight": 0.5},
                {"code": "000001", "sector": "银行", "weight": 0.3},
            ],
        }
        market_data = {
            "benchmark": {"return": 0.05},
            "sector_returns": {"白酒": 0.08, "银行": 0.04},
            "stock_returns": {"600519": 0.15, "000001": 0.03},
            "factor_exposures": {"value": 0.3, "momentum": 0.2},
            "factor_returns": {"value": 0.02, "momentum": 0.05},
        }
        result = attr.analyze(portfolio, market_data)
        assert isinstance(result, AttributionResult)
        assert result.total_return == 0.10

    def test_brinson_4_components_sum_to_total(self):
        """P1-8: 4 个 component 之和应 = total_return

        旧逻辑：factor_return = total - factor_contribution（错误）
        新逻辑：factor_return = total - (market + sector + stock)
        """
        attr = self._make()
        portfolio = {
            "total_return": 0.12,  # 12% 总收益
            "positions": [
                {"code": "600519", "sector": "白酒", "weight": 0.6},
                {"code": "000001", "sector": "银行", "weight": 0.4},
            ],
        }
        market_data = {
            "benchmark": {"return": 0.04},
            "sector_returns": {"白酒": 0.06, "银行": 0.05},
            "stock_returns": {"600519": 0.20, "000001": 0.02},
            "factor_exposures": {"value": 0.5, "momentum": 0.3},
            "factor_returns": {"value": 0.03, "momentum": 0.04},
        }
        result = attr.analyze(portfolio, market_data)

        # 关键：4 个 return 字段之和 = total_return
        sum_components = (
            result.market_return
            + result.sector_return
            + result.stock_return
            + result.factor_return
        )
        assert abs(sum_components - result.total_return) < 1e-6, (
            f"market={result.market_return:.4f}, sector={result.sector_return:.4f}, "
            f"stock={result.stock_return:.4f}, factor={result.factor_return:.4f}, "
            f"sum={sum_components:.4f}, total={result.total_return:.4f}, "
            f"diff={sum_components - result.total_return:.4f}"
        )

    def test_brinson_sums_with_realistic_data(self):
        """P1-8: 真实场景下 4 维加和也闭合"""
        attr = self._make()
        # 100% 持仓一只股票，10% 收益
        portfolio = {
            "total_return": 0.10,
            "positions": [{"code": "600519", "sector": "白酒", "weight": 1.0}],
        }
        market_data = {
            "benchmark": {"return": 0.05},
            "sector_returns": {"白酒": 0.07},
            "stock_returns": {"600519": 0.10},
            "factor_exposures": {"value": 1.0, "momentum": 0.5},
            "factor_returns": {"value": 0.02, "momentum": 0.01},
        }
        result = attr.analyze(portfolio, market_data)
        sum_components = (
            result.market_return + result.sector_return
            + result.stock_return + result.factor_return
        )
        assert abs(sum_components - result.total_return) < 1e-6
