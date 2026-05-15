"""因子评价模块单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd
from lib.evaluator import FactorEvaluator, EvalConfig, FactorScorecard, ICRecord


class TestFactorEvaluator:
    def test_evaluator_init(self):
        """FactorEvaluator 可正常实例化"""
        eval_cfg = EvalConfig()
        evaluator = FactorEvaluator(eval_cfg)
        assert evaluator.config is not None

    def test_eval_config_defaults(self):
        """EvalConfig 默认值正确"""
        cfg = EvalConfig()
        assert 1 in cfg.ic_periods
        assert 5 in cfg.ic_periods
        assert cfg.n_quantiles == 5
        assert cfg.redundancy_threshold == 0.70

    def test_ic_record_creation(self):
        """ICRecord 可正常创建"""
        rec = ICRecord(
            date="2026-01-05", factor="rsi_14",
            ic=0.05, rank_ic=0.08,
            ic_pvalue=0.1, rank_ic_pvalue=0.05,
            n_samples=100
        )
        assert rec.factor == "rsi_14"
        assert rec.ic == 0.05
        assert rec.rank_ic == 0.08

    def test_factor_scorecard_creation(self):
        """FactorScorecard 可正常创建"""
        fs = FactorScorecard(factor="rsi_14", rank_ic_mean=0.06, rank_ic_std=0.02)
        assert fs.factor == "rsi_14"
        assert fs.rank_ic_mean == 0.06

    def test_compute_ic_with_valid_data(self):
        """IC 计算返回有效值"""
        evaluator = FactorEvaluator(EvalConfig())
        factor_vals = pd.Series(np.random.randn(100))
        forward_rets = pd.Series(np.random.randn(100))
        ic_result = evaluator.compute_ic(factor_vals, forward_rets)
        assert isinstance(ic_result, dict)
        assert "rank_ic" in ic_result or "ic" in ic_result

    def test_compute_ic_insufficient_samples(self):
        """样本不足时返回无效"""
        evaluator = FactorEvaluator(EvalConfig())
        factor_vals = pd.Series([1, 2, 3])
        forward_rets = pd.Series([0.1, 0.2, 0.3])
        ic_result = evaluator.compute_ic(factor_vals, forward_rets)
        assert isinstance(ic_result, dict)
        assert ic_result.get("valid", True) is False or np.isnan(ic_result.get("rank_ic", 0))


class TestICAnalysis:
    def test_ic_periods_config(self):
        """IC 周期配置支持多周期"""
        cfg = EvalConfig(ic_periods=[1, 5, 10, 20])
        assert len(cfg.ic_periods) == 4
        assert 20 in cfg.ic_periods

    def test_decay_periods_config(self):
        """衰减周期配置"""
        cfg = EvalConfig(decay_periods=[1, 3, 5, 10])
        assert 10 in cfg.decay_periods
        assert 1 in cfg.decay_periods
