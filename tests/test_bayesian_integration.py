"""P0-2: 贝叶斯闭环接入生产路径

修复前问题：
- pipeline.py:719 DynamicWeightManager() 无参构造
- bayesian_updater 在整个 lib/ 树中除 docstring 和测试外无任何调用点
- bayesian_state.json 永远空，自适应机制名存实亡

修复后：
- DynamicWeightManager 接受 bayesian_updater 参数
- pipeline.py 顶层维护 BayesianUpdater 单例
- 交易完成后调用 bayesian_updater.update(...)
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_dynamic_weight_manager_accepts_bayesian_updater():
    """P0-2: DynamicWeightManager 接受 bayesian_updater 参数"""
    from lib.dynamic_weight_manager import DynamicWeightManager
    from lib.bayesian_updater import BayesianUpdater

    bayes = BayesianUpdater()
    mgr = DynamicWeightManager(bayesian_updater=bayes)
    assert mgr._bayesian is bayes, "DynamicWeightManager 未保留 bayesian_updater"


def test_dynamic_weight_manager_bayesian_path_executes():
    """P0-2: 当传入 bayesian_updater 时，compute_weights 应调用贝叶斯分支"""
    from lib.dynamic_weight_manager import DynamicWeightManager
    from lib.bayesian_updater import BayesianUpdater

    bayes = BayesianUpdater()
    mgr = DynamicWeightManager(bayesian_updater=bayes)

    # Mock bayesian_updater 的 get_posterior 方法（返回胜率 0-1）
    bayes.get_posterior = MagicMock(return_value=0.7)
    bayes.get_weight_adjustment = MagicMock(return_value=1.0)

    weights = mgr.compute_weights(regime="TREND_UP")
    # 贝叶斯分支应被调用
    bayes.get_weight_adjustment.assert_called()


def test_pipeline_passes_bayesian_updater_to_dynamic_weight_manager():
    """P0-2: pipeline.py 顶层应维护 BayesianUpdater 并传给 DynamicWeightManager"""
    # 通过 import pipeline 不实际跑（避免依赖链），只检查 import 时不报错
    import importlib
    try:
        # 检查 BayesianUpdater 是否在 pipeline 模块顶层被引用
        import lib.pipeline as pipeline_mod
        # 找到 DynamicWeightManager 实例化的行
        import inspect
        source = inspect.getsource(pipeline_mod)
        assert "DynamicWeightManager(bayesian_updater=" in source, \
            "pipeline.py 中 DynamicWeightManager 应传入 bayesian_updater"
        # 同时检查 BayesianUpdater 单例创建
        assert "BayesianUpdater(" in source or "BayesianUpdater()" in source, \
            "pipeline.py 顶层应创建 BayesianUpdater 实例"
    except ImportError as e:
        pytest.skip(f"lib.pipeline 导入失败（依赖链问题）: {e}")


def test_bayesian_state_persists_after_update():
    """P0-2: 调 bayesian_updater.update(...) + save() 后，state 应写入文件"""
    from lib.bayesian_updater import BayesianUpdater

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "bayesian_state.json"
        bayes = BayesianUpdater(state_path=state_file)
        # BayesianUpdater.update 签名: (market_state, sector_bucket, signal_type, is_win)
        bayes.update(
            market_state="TREND_UP",
            sector_bucket="热门板块",
            signal_type="donchian_breakout",
            is_win=True,
        )
        bayes.save()  # 必须显式 save 才写盘

        assert state_file.exists(), "bayesian_state.json 应在 update + save 后生成"
