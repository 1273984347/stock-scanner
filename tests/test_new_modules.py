"""新模块测试 —— 覆盖本次新增的核心模块

测试范围：
- lib/factor_optimizer.py — 因子权重优化器
- lib/dynamic_exit.py — 动态退出规则
- lib/tdx_batch.py — TDX 批量读取
- lib/backtest_unified.py — 统一回测接口
- lib/data_margin.py — 融资融券数据
- lib/sentiment.py — 情绪分析
"""

import pytest
import numpy as np
from unittest.mock import patch, MagicMock


# ========== factor_optimizer ==========

class TestFactorOptimizer:
    def test_normalize_weights(self):
        from lib.scoring.factor_optimizer import _normalize_weights
        weights = {"a": 0.5, "b": 0.3, "c": 0.2}
        result = _normalize_weights(weights)
        assert abs(sum(result.values()) - 1.0) < 0.01

    def test_clamp_weights(self):
        from lib.scoring.factor_optimizer import _clamp_weights
        weights = {"a": 0.5, "b": -0.5, "c": 0.1}
        result = _clamp_weights(weights)
        assert result["a"] == 0.30  # clamped to max
        assert result["b"] == -0.30  # clamped to min
        assert result["c"] == 0.1

    def test_random_weights(self):
        from lib.scoring.factor_optimizer import _random_weights, FACTOR_NAMES
        rng = np.random.default_rng(42)
        weights = _random_weights(rng)
        assert len(weights) == len(FACTOR_NAMES)
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_weights_hash(self):
        from lib.scoring.factor_optimizer import _weights_hash
        w1 = {"a": 0.5, "b": 0.5}
        w2 = {"a": 0.5, "b": 0.5}
        w3 = {"a": 0.6, "b": 0.4}
        assert _weights_hash(w1) == _weights_hash(w2)
        assert _weights_hash(w1) != _weights_hash(w3)

    def test_optimizer_config_defaults(self):
        from lib.scoring.factor_optimizer import OptimizerConfig
        config = OptimizerConfig()
        assert config.population_size == 50
        assert config.generations == 100
        assert config.crossover_rate == 0.8

    def test_apply_optimal_weights(self):
        from lib.scoring.factor_optimizer import apply_optimal_weights, FACTOR_NAMES
        weights = {k: 1.0 / len(FACTOR_NAMES) for k in FACTOR_NAMES}
        code = apply_optimal_weights(weights)
        assert "IC_FACTOR_WEIGHTS" in code
        assert "volume_ratio" in code


# ========== dynamic_exit ==========
# P0-2 (2026-06-08): 删除 DynamicLossBreaker（死代码）— 仅保留 VolatilityPositionSizer,
# DynamicTakeProfit, DynamicExitManager 三个非死代码组件的测试。

class TestDynamicExit:
    def test_volatility_position_sizer(self):
        from lib.risk.dynamic_exit import VolatilityPositionSizer
        sizer = VolatilityPositionSizer()

        low_vol_pos = sizer.get_position_pct(0.15)
        high_vol_pos = sizer.get_position_pct(0.50)
        assert low_vol_pos > high_vol_pos

    def test_dynamic_take_profit(self):
        from lib.risk.dynamic_exit import DynamicTakeProfit
        tp = DynamicTakeProfit()

        low_vol_levels = tp.get_exit_levels(0.15)
        high_vol_levels = tp.get_exit_levels(0.50)
        # 高波动时止盈目标更低
        assert high_vol_levels[0][0] < low_vol_levels[0][0]

    def test_dynamic_exit_manager(self):
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()

        mgr.update_market_state("TREND_UP", 0.25)
        status = mgr.get_status()
        assert "loss_breaker" in status
        assert "position_pct" in status
        assert "exit_levels" in status

    def test_get_exit_days_trend_up(self):
        """TREND_UP regime should give 20 days (V6 design: let profits run)"""
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()
        mgr.update_market_state("TREND_UP", 0.25)
        assert mgr.get_exit_days() == 20

    def test_get_exit_days_mean_reversion(self):
        """MEAN_REVERSION regime should give 10 days (V6 design: quick in/out)"""
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()
        mgr.update_market_state("MEAN_REVERSION", 0.25)
        assert mgr.get_exit_days() == 10

    def test_get_exit_days_trend_down(self):
        """TREND_DOWN regime should give 0 days (V6 design: no entry)"""
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()
        mgr.update_market_state("TREND_DOWN", 0.25)
        assert mgr.get_exit_days() == 0

    def test_get_exit_days_risk_off(self):
        """RISK_OFF regime should give 0 days (exit immediately)"""
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()
        mgr.update_market_state("RISK_OFF", 0.25)
        assert mgr.get_exit_days() == 0

    def test_get_exit_days_low_volatility(self):
        """LOW_VOLATILITY regime should give 15 days"""
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()
        mgr.update_market_state("LOW_VOLATILITY", 0.25)
        assert mgr.get_exit_days() == 15

    def test_get_exit_days_unknown_regime(self):
        """Unknown regime should use default (10 days)"""
        from lib.risk.dynamic_exit import DynamicExitManager
        mgr = DynamicExitManager()
        mgr.update_market_state("UNKNOWN_REGIME", 0.25)
        days = mgr.get_exit_days()
        assert days == 10  # default fallback per actual implementation


# ========== tdx_batch ==========

class TestTDXBatch:
    def test_read_closes_only(self):
        from lib.data.tdx_batch import _read_closes_only
        from pathlib import Path
        # 找一个存在的文件
        test_file = Path("D:/TDX/vipdoc/sh/lday_4/sh600519.day")
        if test_file.exists():
            closes = _read_closes_only(test_file, days=30)
            assert closes is not None
            assert len(closes) == 30
            assert all(c > 0 for c in closes)

    def test_ema(self):
        from lib.data.tdx_batch import _ema
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _ema(data, 3)
        assert len(result) == len(data)
        assert result[0] == 1.0

    def test_rsi(self):
        from lib.data.tdx_batch import _rsi
        # 持续上涨应该 RSI 接近 100
        data = np.array(range(1, 30), dtype=float)
        rsi = _rsi(data, 14)
        assert rsi[-1] > 50

    def test_atr(self):
        from lib.data.tdx_batch import _atr
        highs = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
        lows = np.array([9.0, 10.0, 11.0, 12.0, 13.0])
        closes = np.array([9.5, 10.5, 11.5, 12.5, 13.5])
        result = _atr(highs, lows, closes, 3)
        assert len(result) == 5
        assert result[-1] > 0

    def test_batch_read_closes(self):
        from lib.data.tdx_batch import batch_read_closes
        from lib.data.market_utils import get_tdx_path
        # CI 环境有 D:\TDX\vipdoc 目录但无 600519.day 股票文件, 必须检查具体文件
        test_path = get_tdx_path("600519", prefer_qfq=True)
        if test_path is None or not test_path.exists():
            pytest.skip(f"TDX 股票文件 {test_path} 不可用（CI 环境）")
        result = batch_read_closes(["600519", "000858"], days=30)
        # 至少应该读到一只
        assert len(result) >= 1


# ========== sentiment ==========

class TestSentiment:
    def test_score_text_positive(self):
        from lib.scoring.sentiment import _score_text
        score = _score_text("利好上涨突破新高")
        assert score > 0

    def test_score_text_negative(self):
        from lib.scoring.sentiment import _score_text
        score = _score_text("暴跌跌停爆雷亏损")
        assert score < 0

    def test_score_text_neutral(self):
        from lib.scoring.sentiment import _score_text
        score = _score_text("今天天气不错")
        assert score == 0.0

    def test_score_text_empty(self):
        from lib.scoring.sentiment import _score_text
        score = _score_text("")
        assert score == 0.0

    def test_sector_sentiment(self):
        from lib.scoring.sentiment import _score_text
        score = _score_text("国产替代芯片突破", sector="半导体")
        assert score > 0

    def test_get_sentiment_factor(self):
        from lib.scoring.sentiment import get_sentiment_factor
        factor = get_sentiment_factor("600519")
        assert -100 <= factor <= 100

    def test_get_market_sentiment_structure(self):
        from lib.scoring.sentiment import get_market_sentiment
        result = get_market_sentiment()
        assert "market_sentiment" in result
        assert "fear_greed" in result
        assert 0 <= result["fear_greed"] <= 100


# ========== backtest_unified ==========

class TestBacktestUnified:
    def test_backtest_request_defaults(self):
        from lib.monitoring.backtest_unified import BacktestRequest
        req = BacktestRequest()
        assert req.mode == "factor"
        assert req.days == 300

    def test_runner_modes(self):
        from lib.monitoring.backtest_unified import BacktestRunner
        runner = BacktestRunner()
        # 测试未知模式
        result = runner.run(mode="unknown", symbols=["600519"])
        assert result is None

    def test_runner_no_symbols(self):
        from lib.monitoring.backtest_unified import BacktestRunner
        runner = BacktestRunner()
        result = runner.run(mode="factor", symbols=[])
        assert result is None


# ========== data_margin ==========

class TestMarginData:
    def test_get_margin_indicator_structure(self):
        from lib.data.data_margin import get_margin_indicator
        # lib.data.data_margin._safe_call 的退避是硬编码 time.sleep(2 ** attempt)
        # （不是模块常量，故无法像 data_layer 那样 patch 常量），
        # 未短路 => 每个 API 调用真等 3s，本用例实测 6.65s。
        # 禁网下真实数据取不到（返回默认值），断言只校验结构，不依赖真实等待。
        with patch("lib.data.data_margin.time.sleep"):
            result = get_margin_indicator("600519")
        assert "margin_balance" in result
        assert "margin_balance_chg" in result
        assert "margin_buy_ratio" in result
        assert "margin_sentiment" in result
        assert "short_balance" in result
        assert "short_ratio" in result

    def test_margin_balance_positive(self):
        from lib.data.data_margin import get_margin_indicator
        # 同上：短路 _safe_call 的退避 sleep
        with patch("lib.data.data_margin.time.sleep"):
            result = get_margin_indicator("600519")
        # 茅台应该有正的融资余额
        assert result["margin_balance"] >= 0

    def test_clear_cache(self):
        from lib.data.data_margin import clear_cache, _MARGIN_CACHE
        _MARGIN_CACHE["test"] = "value"
        clear_cache()
        assert len(_MARGIN_CACHE) == 0


# ========== backtest deprecation ==========

class TestBacktestDeprecation:
    def test_rebalance_deprecation_warning(self):
        """rebalance.run_rebalance_backtest should emit DeprecationWarning"""
        import warnings
        from backtest.rebalance import run_rebalance_backtest
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                run_rebalance_backtest(symbols=["000001"], days=60)
            except Exception:
                pass  # May fail due to missing data, we just want the warning
            deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_warnings) > 0
            assert "deprecated" in str(deprecation_warnings[0].message).lower()
