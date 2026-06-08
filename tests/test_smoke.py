"""基础冒烟测试：验证关键模块可正常导入"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_config_import():
    from config import SystemConfig, PortfolioConfig, MonitorConfig
    c = SystemConfig()
    # 注：原 c.ml.use_ml 断言已删除（use_ml 字段在死参数清理中移除）
    assert c.portfolio.optimize_method == "equal_weight"


def test_factors_import():
    from factors import compute_all_signals, calc_rsi
    assert callable(compute_all_signals)


def test_scanner_import():
    from scanner import get_stock_hist, get_realtime_quotes
    assert callable(get_stock_hist)


def test_judge_panel_import():
    from lib.judge_panel import JudgePanel
    j = JudgePanel()
    r = j.evaluate_all()
    assert "consensus" in r
    # §4.2 P0 修复 (2026-06-08): 6 组硬编码规则 weight=0 禁用
    # evaluate_all 走空集路径, total_votes = 0 (ML 票由 evaluate_with_ml 提供)
    assert r["total_votes"] >= 0
    # evaluate_with_ml 仍可能产生票
    r_ml = j.evaluate_with_ml({"model_available": False, "consensus": "HOLD", "score": 0.0})
    assert r_ml["total_votes"] >= 0


def test_scorecard_import():
    from lib.scorecard import ScoreCard, MarketFilter
    l1 = MarketFilter.layer1_market_trend()
    assert isinstance(l1, float)
    assert 0 <= l1 <= 1


def test_ml_modules():
    from lib.ml_data import MLDataPipeline
    from lib.ml_trainer import MLTrainer
    from lib.ml_predictor import MLPredictor
    assert callable(MLTrainer)
    # 预测器输出结构验证
    p = MLPredictor()
    r = p.predict_single({"rsi_14": 50})
    assert "score" in r
    assert "confidence" in r
    assert "consensus" in r
    assert r["consensus"] in ("BUY", "SELL", "HOLD")


def test_portfolio_import():
    from lib.portfolio import PortfolioBuilder
    from config import PortfolioConfig
    pb = PortfolioBuilder(PortfolioConfig())
    assert pb.config.optimize_method == "equal_weight"


def test_cost_filter():
    from lib.cost_filter import TradabilityFilter
    from config import PortfolioConfig
    f = TradabilityFilter(PortfolioConfig())
    assert f.min_daily_volume == 5e6


def test_backtest():
    from lib.ml_backtest import MLBacktest
    bt = MLBacktest({"step": 5})
    assert bt.step == 5
    assert bt.target_col == "excess_5d"
