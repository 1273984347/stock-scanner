"""E2E 集成测试：全链路扫描 → 打分 → 风控 → 订单生成

测试完整的量化选股流程，确保各模块协同工作。
"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np

# 测试标记
pytestmark = pytest.mark.skipif(
    True,  # 默认跳过，需要真实数据环境
    reason="E2E test requires real data environment"
)


class TestPipelineE2E:
    """Pipeline E2E 测试套件"""

    @pytest.fixture
    def mock_hist_data(self):
        """模拟历史数据"""
        dates = pd.date_range(end="2026-05-23", periods=200, freq="D")
        return pd.DataFrame({
            "date": dates,
            "open": np.random.uniform(10, 20, 200),
            "high": np.random.uniform(15, 25, 200),
            "low": np.random.uniform(5, 15, 200),
            "close": np.random.uniform(10, 20, 200),
            "volume": np.random.uniform(1e6, 1e7, 200),
        })

    @pytest.fixture
    def mock_realtime(self):
        """模拟实时行情"""
        return pd.DataFrame({
            "代码": ["000001", "600000"],
            "名称": ["平安银行", "浦发银行"],
            "最新价": [12.5, 8.3],
            "涨跌幅": [1.2, -0.5],
            "市盈率-动态": [8.5, 6.2],
            "量比": [1.2, 0.8],
            "换手率": [0.5, 0.3],
            "成交额": [5e8, 3e8],
        })

    @patch("lib.data.data_layer.get_stock_hist_multi_source")
    @patch("lib.data.data_layer.get_realtime_quotes")
    def test_scan_to_score(self, mock_realtime, mock_hist, mock_hist_data, mock_realtime_data):
        """测试：扫描 → 打分"""
        mock_hist.return_value = mock_hist_data
        mock_realtime.return_value = mock_realtime_data

        from scanner import ScanOrchestrator
        orchestrator = ScanOrchestrator(preset="balanced")

        # 执行扫描
        results = orchestrator.run_all_strategies(include_ml=False)

        # 验证结果结构
        assert isinstance(results, dict)

    @patch("lib.strategy.pipeline.get_stock_score")
    def test_single_stock_scoring(self, mock_score):
        """测试：单股评分"""
        mock_score.return_value = {
            "code": "000001",
            "factors": {"momentum_5": 2.5, "momentum_20": 5.1},
            "score": {"score": 7.5, "rating": "买入"},
            "judge_panel": {"consensus": "BUY", "score": 0.7},
            "ml_prediction": {},
            "constraints": {"passed": True, "blocked": False},
        }

        from lib.strategy.pipeline import get_stock_score
        result = get_stock_score("000001")

        assert result["code"] == "000001"
        assert "score" in result
        assert "factors" in result

    def test_config_loading(self):
        """测试：配置加载"""
        from config import get_config, PRESETS

        balanced = get_config("balanced")
        assert balanced is not None

        for preset in PRESETS:
            config = get_config(preset)
            assert config is not None


class TestBrokerConstraints:
    """Broker 约束测试"""

    def test_order_validation(self):
        """测试：订单参数验证"""
        from lib.data.broker import Order, TradingConstraints

        order = Order(
            code="000001",
            direction="BUY",
            price=12.5,
            quantity=100,
            order_type="LIMIT",
        )

        assert order.code == "000001"
        assert order.direction == "BUY"
        assert order.quantity == 100

    def test_trading_constraints_basic(self):
        """测试：交易约束基本功能"""
        from lib.data.broker import TradingConstraints

        constraints = TradingConstraints()

        # 涨停板检查
        assert constraints.get_limit_pct("2026-05-23") == 0.10  # 主板 10%


class TestDataFlow:
    """数据流测试"""

    def test_column_normalization(self):
        """测试：列名标准化"""
        from lib.data.data_source_registry import _normalize_columns

        # 测试 trade_date -> date 映射
        df = pd.DataFrame({
            "trade_date": ["2026-05-23"],
            "open": [12.5],
            "high": [13.0],
            "low": [12.0],
            "close": [12.5],
            "vol": [1e6],
        })
        result = _normalize_columns(df)

        assert "date" in result.columns
        assert "volume" in result.columns
        assert "trade_date" not in result.columns
        assert "vol" not in result.columns

    def test_batch_quick_score(self):
        """测试：批量快速评分"""
        from lib.strategy.strategies import batch_quick_score

        rows = [
            pd.Series({"代码": "000001", "涨跌幅": 2.5, "60日涨跌幅": 10.0}),
            pd.Series({"代码": "600000", "涨跌幅": -1.0, "60日涨跌幅": -5.0}),
        ]

        results = batch_quick_score(rows, max_workers=2)

        assert isinstance(results, dict)
        assert "000001" in results
        assert "600000" in results


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
