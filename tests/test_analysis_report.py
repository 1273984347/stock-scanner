"""分析报告测试：回测路径对比 + 评委团按组验证"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd


class TestBacktestPathComparison:
    """P1-4: 回测路径对比"""

    def test_factor_backtest_runs(self):
        """因子择时路径可运行（不崩溃）"""
        from backtest import run_factor_backtest
        r = run_factor_backtest(["000001", "600519", "000858"], days=250, confidence_threshold=0.3)
        # 可能无交易（信号频率低），但不应崩溃
        assert r is None or "metrics" in r

    def test_ml_backtest_runs(self):
        """ML 截面排序路径可运行（需要 panel 数据）"""
        panel_path = "cache/panel_2015.parquet"
        if not os.path.exists(panel_path):
            pytest.skip("panel_2015.parquet 不存在，跳过 ML 回测")
        panel = pd.read_parquet(panel_path)
        panel["date"] = pd.to_datetime(panel["date"])
        dates = sorted(panel["date"].unique())
        test_dates = set(dates[int(len(dates) * 0.8):])
        p_test = panel[panel["date"].isin(test_dates)].copy()

        import json, xgboost as xgb
        model_path = "cache/models/xgb_20d_v2/v1/manifest.json"
        model_file = "cache/models/xgb_20d_v2/v1/xgb_20d_v2.model"
        if not os.path.exists(model_path) or not os.path.exists(model_file):
            pytest.skip("ML 模型文件不存在，跳过")

        with open(model_path) as f:
            mf = json.load(f)
        model = xgb.Booster()
        model.load_model(model_file)
        feats = [c for c in mf["feature_cols"] if c in panel.columns]
        for c in mf["feature_cols"]:
            if c not in panel.columns:
                panel[c] = 0.5
        ms_all = model.predict(xgb.DMatrix(panel[feats].fillna(0.5)))
        ms_test = pd.Series(ms_all, index=panel.index)[panel["date"].isin(test_dates)]

        from lib.ml_backtest import MLBacktest
        bt = MLBacktest({"target_col": "excess_20d", "top_pct": 0.1, "transaction_cost": 0.01, "step": 20})
        r2 = bt.run(p_test, ml_scores=ms_test)
        assert "metrics" in r2
        assert "ml_only" in r2["metrics"]


class TestJudgePanelGroups:
    """P2-1: 评委团按组验证"""

    def test_judge_panel_groups_exist(self):
        """评委团各组规则可正常导入和实例化"""
        from lib.judge_panel import JudgePanel
        import pandas as pd
        panel = JudgePanel()
        assert hasattr(panel, "evaluate_all")
        result = panel.evaluate_all()
        assert "consensus" in result
        assert "total_votes" in result

    def test_judge_groups_have_abstain_mechanism(self):
        """评委团具备 ABSTAIN 机制（数据不足时正确降级）"""
        from lib.judge_panel import JudgePanel
        panel = JudgePanel()
        # 无市场数据时各组应能处理（ABSTAIN 而非崩溃）
        try:
            result = panel.evaluate_all()
            # ABSTAIN 时 total_votes 可能为 0
            assert isinstance(result, dict)
        except Exception:
            pytest.fail("评委团在缺少数数据时应优雅降级，不应抛出异常")
